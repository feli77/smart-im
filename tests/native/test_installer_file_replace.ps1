<#
.SYNOPSIS
Test TSF DLL replacement using isolated files, including loaded-file behavior.
.DESCRIPTION
Extracts only three installer functions. No registry, installed DLL, server or
system directory is read or modified. Works in Windows PowerShell 5.1 and 7.
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
foreach ($functionName in @("Hash-File", "Check-Hash", "Set-TsfSystemBinary")) {
    $definitions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $functionName
    }, $true))
    if ($definitions.Count -ne 1) { throw "Expected exactly one $functionName function." }
    . ([scriptblock]::Create($definitions[0].Extent.Text))
}

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "FAIL: $Message" }
}

# Failure injection is scoped to this test process; qualified cmdlet calls
# still perform real copies and renames for every other operation.
$script:injectedFailure = $null
$script:originalCheckHash = ${function:Check-Hash}
$script:loadedNewStream = $null
function Check-Hash {
    param([string]$Path, [string]$Expected)
    if ($script:injectedFailure -eq "new-hash" -and $Path -eq $destination -and $Expected -eq $newHash) {
        $script:loadedNewStream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            ([IO.FileShare]::Read -bor [IO.FileShare]::Delete))
        throw "Injected new destination hash failure."
    }
    & $script:originalCheckHash $Path $Expected
}

function Copy-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($script:injectedFailure -eq "stage-copy" -and $Destination -like "*.smart-im-stage-*") {
        [IO.File]::WriteAllText($Destination, "incomplete staged copy")
        throw "Injected staged copy failure."
    }
    Microsoft.PowerShell.Management\Copy-Item @PSBoundParameters
}

function Move-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Destination, [switch]$Force)
    if ($script:injectedFailure -eq "stage-rename" -and $LiteralPath -like "*.smart-im-stage-*") {
        throw "Injected staged rename failure."
    }
    Microsoft.PowerShell.Management\Move-Item @PSBoundParameters
}

function Read-OpenStream {
    param([IO.FileStream]$Stream)
    $Stream.Position = 0
    $bytes = New-Object byte[] $Stream.Length
    $count = $Stream.Read($bytes, 0, $bytes.Length)
    return [Text.Encoding]::ASCII.GetString($bytes, 0, $count)
}

function Assert-NoSideFiles {
    Assert-Test (@(Get-ChildItem -LiteralPath $testRoot -File).Count -eq 2) `
        "Replacement left unexpected staged or old files."
}

$outputRoot = Join-Path $projectRoot "artifacts\native-tests"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$testRoot = Join-Path $outputRoot ("installer-files-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$source = Join-Path $testRoot "source.dll"
$destination = Join-Path $testRoot "destination.dll"
$oldText = "Original isolated test DLL bytes."
$newText = "Patched isolated test DLL bytes."
[IO.File]::WriteAllText($source, $newText, [Text.Encoding]::ASCII)
[IO.File]::WriteAllText($destination, $oldText, [Text.Encoding]::ASCII)
$newHash = Hash-File $source
$oldHash = Hash-File $destination
$heldStream = $null
try {
    $result = Set-TsfSystemBinary $source $destination $newHash
    Assert-Test ($result.Changed -eq $true) "Unlocked replacement did not report a change."
    Check-Hash $destination $newHash
    Assert-NoSideFiles
    Write-Output "PASS: unlocked replacement is verified and cleaned up"

    $beforeWrite = [IO.File]::GetLastWriteTimeUtc($destination)
    $result = Set-TsfSystemBinary $source $destination $newHash
    Assert-Test ($result.Changed -eq $false) "Identical target was replaced again."
    Assert-Test ([IO.File]::GetLastWriteTimeUtc($destination) -eq $beforeWrite) "Idempotent call rewrote the target."
    Assert-NoSideFiles
    Write-Output "PASS: repeat replacement is idempotent"

    [IO.File]::WriteAllText($destination, $oldText, [Text.Encoding]::ASCII)
    $failed = $false
    try { Set-TsfSystemBinary $source $destination $oldHash | Out-Null }
    catch { $failed = $true }
    Assert-Test $failed "Wrong source hash was accepted."
    Check-Hash $destination $oldHash
    Assert-NoSideFiles
    Write-Output "PASS: invalid source hash leaves original untouched"

    $failed = $false
    try { Set-TsfSystemBinary $source $source $newHash | Out-Null }
    catch { $failed = $true }
    Assert-Test $failed "Same source and destination was accepted."
    Check-Hash $source $newHash
    Assert-NoSideFiles
    Write-Output "PASS: source and destination must be different"

    $heldStream = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        ([IO.FileShare]::Read -bor [IO.FileShare]::Delete))
    $overwriteFailed = $false
    try { Microsoft.PowerShell.Management\Copy-Item -LiteralPath $source -Destination $destination -Force }
    catch { $overwriteFailed = $true }
    Assert-Test $overwriteFailed "Test stream failed to prevent an ordinary overwrite."
    $result = Set-TsfSystemBinary $source $destination $newHash
    Assert-Test $result.Changed "Loaded-file replacement did not report a change."
    Check-Hash $destination $newHash
    Assert-Test ((Read-OpenStream $heldStream) -eq $oldText) "An existing reader did not retain the old DLL bytes."
    $heldStream.Dispose()
    $heldStream = $null
    Assert-NoSideFiles
    Write-Output "PASS: shared-delete loaded file is replaced while its reader retains original bytes"

    foreach ($failure in @("stage-copy", "stage-rename")) {
        [IO.File]::WriteAllText($destination, $oldText, [Text.Encoding]::ASCII)
        $script:injectedFailure = $failure
        $failed = $false
        try { Set-TsfSystemBinary $source $destination $newHash | Out-Null }
        catch { $failed = $true }
        finally { $script:injectedFailure = $null }
        Assert-Test $failed "Injected $failure failure was ignored."
        Check-Hash $destination $oldHash
        Assert-NoSideFiles
        Write-Output "PASS: $failure failure preserves or restores the original destination"
    }

    $script:injectedFailure = "new-hash"
    $failed = $false
    try { Set-TsfSystemBinary $source $destination $newHash | Out-Null }
    catch { $failed = $true }
    finally { $script:injectedFailure = $null }
    Assert-Test $failed "Injected verification failure was ignored."
    Check-Hash $destination $oldHash
    Assert-Test ($null -ne $script:loadedNewStream) "Replacement did not reach the new-target verification."
    Assert-Test ((Read-OpenStream $script:loadedNewStream) -eq $newText) "Restoring the original changed an already loaded new DLL."
    $script:loadedNewStream.Dispose()
    $script:loadedNewStream = $null
    Assert-NoSideFiles
    Write-Output "PASS: failed final verification restores original even when new target is loaded"

    $heldStream = [IO.File]::Open($destination, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    $failed = $false
    try { Set-TsfSystemBinary $source $destination $newHash | Out-Null }
    catch { $failed = $true }
    Assert-Test $failed "A target without delete sharing should fail safely."
    Assert-Test ((Read-OpenStream $heldStream) -eq $oldText) "Rename denial changed the original file."
    $heldStream.Dispose()
    $heldStream = $null
    Check-Hash $destination $oldHash
    Assert-NoSideFiles
    Write-Output "PASS: rename denial leaves original target intact"
} finally {
    $script:injectedFailure = $null
    if ($heldStream) { $heldStream.Dispose() }
    if ($script:loadedNewStream) { $script:loadedNewStream.Dispose() }
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    foreach ($file in @(Get-ChildItem -LiteralPath $testRoot -File)) {
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($file.FullName)) -ne $resolvedTestRoot) {
            throw "Refusing cleanup outside the isolated test directory."
        }
        Remove-Item -LiteralPath $file.FullName -Force
    }
    [IO.Directory]::Delete($testRoot, $false)
}
Write-Output "All installer file replacement tests passed."
