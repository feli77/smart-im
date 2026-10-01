<#
.SYNOPSIS
Exercise the installer's bounded process wait without running installation.
.DESCRIPTION
Only Invoke-InstallerProcess is extracted from the installer AST. The tests
launch harmless PowerShell children; no Weasel process, registry or installed
file is touched. Run with Windows PowerShell 5.1 or PowerShell 7 on Windows.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$installer = Join-Path $projectRoot "scripts\install_weasel_tsf.ps1"
$tokens = $null
$parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw "Installer has PowerShell syntax errors: $parseErrors" }
$definitions = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Invoke-InstallerProcess"
}, $true))
if ($definitions.Count -ne 1) { throw "Expected exactly one Invoke-InstallerProcess function." }
# Deliberately avoid dot-sourcing the installer, which performs system changes.
. ([scriptblock]::Create($definitions[0].Extent.Text))

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "FAIL: $Message" }
}

function New-ChildArguments {
    param([string]$Code)
    return @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Code))
    )
}

$shell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $shell -PathType Leaf)) { throw "Windows PowerShell is required." }
$outputRoot = Join-Path $projectRoot "artifacts\native-tests"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$testRoot = Join-Path $outputRoot ("installer-process-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$pidFile = Join-Path $testRoot "sleeping-child.pid"
$descendantPidFile = Join-Path $testRoot "sleeping-descendant.pid"
$parentId = $PID
$parentStart = (Get-Process -Id $parentId).StartTime
$unrelated = $null
$sleepingChildId = $null
$descendantId = $null

try {
    foreach ($expected in @(0, 23)) {
        $result = Invoke-InstallerProcess -FilePath $shell `
            -ArgumentList (New-ChildArguments "exit $expected") `
            -TimeoutSeconds 10 -Stage "exit-$expected"
        Assert-Test ($result -is [int]) "Exit code must be returned as a single integer."
        Assert-Test ($result -eq $expected) "Expected exit code $expected, received $result."
        Write-Output "PASS: child exit code $expected"
    }

    # A separate process must survive the timeout. Killing by image name would
    # incorrectly terminate this child and potentially the caller's shell.
    $unrelated = Start-Process -FilePath $shell -WindowStyle Hidden -PassThru `
        -ArgumentList (New-ChildArguments 'while ($true) { Start-Sleep -Seconds 60 }')
    $escapedPidFile = $pidFile.Replace("'", "''")
    $escapedDescendantPidFile = $descendantPidFile.Replace("'", "''")
    $descendantCode = "[IO.File]::WriteAllText('$escapedDescendantPidFile', [string]`$PID); while (`$true) { Start-Sleep -Seconds 60 }"
    $descendantEncoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($descendantCode))
    $escapedShell = $shell.Replace("'", "''")
    $sleepCode = @"
[IO.File]::WriteAllText('$escapedPidFile', [string]`$PID)
Start-Process -FilePath '$escapedShell' -WindowStyle Hidden -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', '$descendantEncoded')
while (`$true) { Start-Sleep -Seconds 60 }
"@
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $timeoutMessage = $null
    try {
        Invoke-InstallerProcess -FilePath $shell -ArgumentList (New-ChildArguments $sleepCode) `
            -TimeoutSeconds 3 -Stage "test-infinite-child" | Out-Null
    } catch {
        $timeoutMessage = $_.Exception.Message
    } finally {
        $watch.Stop()
    }
    Assert-Test ($null -ne $timeoutMessage) "Infinite child must cause an exception."
    Assert-Test ($timeoutMessage -like "*test-infinite-child*") "Timeout must identify its stage."
    Assert-Test ($watch.Elapsed.TotalSeconds -ge 2.5) "Child did not reach the configured wait timeout."
    Assert-Test ($watch.Elapsed.TotalSeconds -lt 15) "Timeout cleanup exceeded the bounded wait."
    Assert-Test (Test-Path -LiteralPath $pidFile -PathType Leaf) "Sleeping child never reached its test code."
    $sleepingChildId = [int](Get-Content -LiteralPath $pidFile -Raw)
    Assert-Test ($sleepingChildId -ne $parentId) "Recorded child PID unexpectedly matches the parent."
    Assert-Test ($null -eq (Get-Process -Id $sleepingChildId -ErrorAction SilentlyContinue)) `
        "Timed-out child $sleepingChildId was left running."
    Assert-Test (Test-Path -LiteralPath $descendantPidFile -PathType Leaf) "Descendant never reached its test code."
    $descendantId = [int](Get-Content -LiteralPath $descendantPidFile -Raw)
    Assert-Test ($descendantId -ne $parentId) "Recorded descendant PID unexpectedly matches the parent."
    Assert-Test ($null -eq (Get-Process -Id $descendantId -ErrorAction SilentlyContinue)) `
        "Timed-out child's descendant $descendantId was left running."
    $unrelated.Refresh()
    Assert-Test (-not $unrelated.HasExited) "Timeout terminated an unrelated PowerShell child."
    $parent = Get-Process -Id $parentId
    Assert-Test ($parent.StartTime -eq $parentStart) "Parent shell was terminated or replaced."
    Write-Output ("PASS: infinite child and descendant stopped after {0:N2}s; parent and unrelated child survived" -f $watch.Elapsed.TotalSeconds)
} finally {
    # Only process IDs owned by this test are eligible for cleanup.
    if ($null -eq $sleepingChildId -and (Test-Path -LiteralPath $pidFile -PathType Leaf)) {
        $sleepingChildId = [int](Get-Content -LiteralPath $pidFile -Raw)
    }
    if ($null -eq $descendantId -and (Test-Path -LiteralPath $descendantPidFile -PathType Leaf)) {
        $descendantId = [int](Get-Content -LiteralPath $descendantPidFile -Raw)
    }
    foreach ($ownedId in @($sleepingChildId, $descendantId)) {
        if ($ownedId -and $ownedId -ne $parentId) {
            $leftover = Get-Process -Id $ownedId -ErrorAction SilentlyContinue
            if ($leftover) { $leftover.Kill(); $leftover.WaitForExit(5000) | Out-Null; $leftover.Dispose() }
        }
    }
    if ($unrelated) {
        if (-not $unrelated.HasExited) { $unrelated.Kill(); $unrelated.WaitForExit(5000) | Out-Null }
        $unrelated.Dispose()
    }
    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile }
    if (Test-Path -LiteralPath $descendantPidFile) { Remove-Item -LiteralPath $descendantPidFile }
    # Non-recursive removal fails safely if unexpected files appeared.
    [IO.Directory]::Delete($testRoot, $false)
}

Write-Output "All installer process tests passed."
