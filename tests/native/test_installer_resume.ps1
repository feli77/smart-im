<#
.SYNOPSIS
Verify resume preflight using isolated fixtures without running installation.
.DESCRIPTION
Extracts only read-only functions from the installer AST. No installed file,
Weasel process, registration or user setting is touched.
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
$readOnlyFunctions = @(
    "Hash-File", "Check-Hash", "Check-PackageBinary", "Read-VerifiedPackage",
    "Read-VerifiedBackup", "Check-ResumeState"
)
foreach ($functionName in $readOnlyFunctions) {
    $definitions = @($ast.FindAll({
        param($node)
        $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName
    }, $true))
    if ($definitions.Count -ne 1) { throw "Expected exactly one $functionName function." }
    . ([scriptblock]::Create($definitions[0].Extent.Text))
}
# A separate inert function checks parameter binding without invoking the script.
. ([scriptblock]::Create("function Test-InstallerParameters { $($ast.ParamBlock.Extent.Text) }"))

$script:checks = 0
function Assert-Rejected {
    param([scriptblock]$Action, [string]$Pattern, [string]$Label)
    $message = $null
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    if ($null -eq $message -or $message -notlike $Pattern) {
        throw "FAIL: $Label; expected '$Pattern', received '$message'."
    }
    $script:checks++
}

function Assert-Accepted {
    param([scriptblock]$Action)
    & $Action | Out-Null
    $script:checks++
}

function New-FixtureBinary {
    param([string]$Path, [string]$Name, [byte]$Version)
    # Minimal PE-shaped fixture: never loaded/executed, only parsed and hashed.
    $bytes = New-Object byte[] 512
    [BitConverter]::GetBytes([int]128).CopyTo($bytes, 60)
    [BitConverter]::GetBytes([uint32]0x00004550).CopyTo($bytes, 128)
    $machine = if ($Name -eq "weasel.dll") { 0x014c } else { 0x8664 }
    [BitConverter]::GetBytes([uint16]$machine).CopyTo($bytes, 132)
    $marker = if ($Name -eq "WeaselServer.exe") {
        [Text.Encoding]::ASCII.GetBytes("smart_im_context")
    } else {
        [Text.Encoding]::Unicode.GetBytes("SmartIMContextProtocol")
    }
    $marker.CopyTo($bytes, 256)
    $bytes[510] = $Version
    [IO.File]::WriteAllBytes($Path, $bytes)
}

$outputRoot = Join-Path $projectRoot "artifacts\native-tests"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$testRoot = Join-Path $outputRoot ("installer-resume-" + [guid]::NewGuid().ToString("N"))
$backupRoot = Join-Path $testRoot "backup"
$installRoot = Join-Path $testRoot "installed"
$packageRoot = Join-Path $testRoot "package"
$systemRoot = Join-Path $testRoot "system"
$backupInstalled = Join-Path $backupRoot "installed"
$backupSystem = Join-Path $backupRoot "system"
$directories = @($testRoot, $backupRoot, $installRoot, $packageRoot, $systemRoot, $backupInstalled, $backupSystem)
foreach ($directory in $directories) { New-Item -ItemType Directory -Path $directory | Out-Null }
$names = @("WeaselServer.exe", "weaselx64.dll", "weasel.dll")
$systemDlls = @{}
$originalHashes = [ordered]@{}
$targetHashes = [ordered]@{}
$systemHashes = [ordered]@{}
$setup = Join-Path $installRoot "WeaselSetup.exe"
$backupManifest = Join-Path $backupRoot "backup.json"
$packageManifest = Join-Path $packageRoot "manifest.json"
$ownedFiles = @($setup, (Join-Path $backupRoot "WeaselSetup.exe"), $backupManifest, $packageManifest)

try {
    foreach ($name in $names) {
        $original = Join-Path $backupInstalled $name
        $target = Join-Path $packageRoot $name
        $installed = Join-Path $installRoot $name
        $ownedFiles += @($original, $target, $installed)
        New-FixtureBinary $original $name 1
        New-FixtureBinary $target $name 2
        Copy-Item -LiteralPath $original -Destination $installed
        $originalHashes[$name] = Hash-File $original
        $targetHashes[$name] = Hash-File $target
        if ($name -ne "WeaselServer.exe") {
            $systemDlls[$name] = Join-Path $systemRoot $name
            $backup = Join-Path $backupSystem $name
            $ownedFiles += @($systemDlls[$name], $backup)
            Copy-Item -LiteralPath $original -Destination $systemDlls[$name]
            Copy-Item -LiteralPath $original -Destination $backup
            $systemHashes[$name] = $originalHashes[$name]
        }
    }
    [IO.File]::WriteAllText($setup, "fixture setup")
    Copy-Item -LiteralPath $setup -Destination (Join-Path $backupRoot "WeaselSetup.exe")
    $backupObject = [pscustomobject]@{
        kind = "smart-im-weasel-tsf-backup"; version = 1; complete = $true
        install_root = $installRoot; hant = 0; setup_sha256 = Hash-File $setup
        files = $originalHashes; system_files = $systemHashes
    }
    $backupJson = $backupObject | ConvertTo-Json -Depth 6
    $packageJson = [pscustomobject]@{ files = $targetHashes } | ConvertTo-Json -Depth 6
    $backupJson | Set-Content -LiteralPath $backupManifest -Encoding UTF8
    $packageJson | Set-Content -LiteralPath $packageManifest -Encoding UTF8
    $saved = Read-VerifiedBackup $backupRoot $installRoot $setup
    $package = Read-VerifiedPackage $packageRoot
    $checkState = { Check-ResumeState $installRoot $systemDlls $saved.files $package.files }
    Assert-Accepted $checkState

    # The observed interrupted installation: installation directory is patched,
    # while both registered system DLLs remain the original version.
    foreach ($name in $names) {
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination (Join-Path $installRoot $name) -Force
    }
    Assert-Accepted $checkState
    Copy-Item -LiteralPath (Join-Path $backupInstalled "weasel.dll") -Destination (Join-Path $installRoot "weasel.dll") -Force
    Copy-Item -LiteralPath (Join-Path $packageRoot "weaselx64.dll") -Destination $systemDlls["weaselx64.dll"] -Force
    Assert-Accepted $checkState
    foreach ($name in $names) {
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination (Join-Path $installRoot $name) -Force
        if ($systemDlls.ContainsKey($name)) {
            Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination $systemDlls[$name] -Force
        }
    }
    Assert-Accepted $checkState

    foreach ($name in $names) {
        $installed = Join-Path $installRoot $name
        New-FixtureBinary $installed $name 99
        Assert-Rejected $checkState "*Resume refused: installed file*" "unknown installed $name"
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination $installed -Force
    }
    foreach ($name in $systemDlls.Keys) {
        New-FixtureBinary $systemDlls[$name] $name 99
        Assert-Rejected $checkState "*Resume refused: system DLL*" "unknown system $name"
        Copy-Item -LiteralPath (Join-Path $packageRoot $name) -Destination $systemDlls[$name] -Force
    }

    $checkBackup = { Read-VerifiedBackup $backupRoot $installRoot $setup }
    foreach ($name in $names) {
        $path = Join-Path $backupInstalled $name
        New-FixtureBinary $path $name 99
        Assert-Rejected $checkBackup "*SHA256 mismatch*" "corrupt original $name"
        New-FixtureBinary $path $name 1
    }
    foreach ($name in $systemDlls.Keys) {
        $path = Join-Path $backupSystem $name
        New-FixtureBinary $path $name 99
        Assert-Rejected $checkBackup "*SHA256 mismatch*" "corrupt system backup $name"
        New-FixtureBinary $path $name 1
    }
    foreach ($badField in @(
        @{ Name = "kind"; Value = "other-backup" },
        @{ Name = "version"; Value = 2 },
        @{ Name = "complete"; Value = $false },
        @{ Name = "complete"; Value = "true" },
        @{ Name = "install_root"; Value = (Join-Path $testRoot "different-installation") },
        @{ Name = "hant"; Value = 1 }
    )) {
        $changed = $backupJson | ConvertFrom-Json
        $changed.($badField.Name) = $badField.Value
        $changed | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $backupManifest -Encoding UTF8
        Assert-Rejected $checkBackup "*Backup is incomplete*" "invalid $($badField.Name)"
    }
    $backupJson | Set-Content -LiteralPath $backupManifest -Encoding UTF8
    foreach ($path in @($setup, (Join-Path $backupRoot "WeaselSetup.exe"))) {
        [IO.File]::WriteAllText($path, "unexpected setup")
        Assert-Rejected $checkBackup "*SHA256 mismatch*" "setup hash changed"
        [IO.File]::WriteAllText($path, "fixture setup")
    }
    $differentSystem = Join-Path $backupSystem "weasel.dll"
    New-FixtureBinary $differentSystem "weasel.dll" 99
    $changed = $backupJson | ConvertFrom-Json
    $changed.system_files.'weasel.dll' = Hash-File $differentSystem
    $changed | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $backupManifest -Encoding UTF8
    Assert-Rejected $checkBackup "*Original system DLLs differ*" "inconsistent original source/system"
    New-FixtureBinary $differentSystem "weasel.dll" 1
    $backupJson | Set-Content -LiteralPath $backupManifest -Encoding UTF8

    $checkPackage = { Read-VerifiedPackage $packageRoot }
    foreach ($name in $names) {
        $path = Join-Path $packageRoot $name
        New-FixtureBinary $path $name 99
        Assert-Rejected $checkPackage "*SHA256 mismatch*" "package hash changed $name"
        New-FixtureBinary $path $name 2
        $bytes = [IO.File]::ReadAllBytes($path)
        [BitConverter]::GetBytes([uint16]0xAA64).CopyTo($bytes, 132)
        [IO.File]::WriteAllBytes($path, $bytes)
        $changed = $packageJson | ConvertFrom-Json
        $changed.files.$name = Hash-File $path
        $changed | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $packageManifest -Encoding UTF8
        Assert-Rejected $checkPackage "*Wrong binary architecture*" "wrong machine $name"
        New-FixtureBinary $path $name 2
        $bytes = [IO.File]::ReadAllBytes($path)
        [Array]::Clear($bytes, 256, 100)
        [IO.File]::WriteAllBytes($path, $bytes)
        $changed.files.$name = Hash-File $path
        $changed | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $packageManifest -Encoding UTF8
        Assert-Rejected $checkPackage "*feature marker is missing*" "unpatched binary $name"
        New-FixtureBinary $path $name 2
        $packageJson | Set-Content -LiteralPath $packageManifest -Encoding UTF8
    }
    $changed = $packageJson | ConvertFrom-Json
    $changed.files | Add-Member -NotePropertyName "extra.dll" -NotePropertyValue ("0" * 64)
    $changed | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $packageManifest -Encoding UTF8
    Assert-Rejected $checkPackage "*exactly the three native files*" "extra package file"
    $packageJson | Set-Content -LiteralPath $packageManifest -Encoding UTF8

    Assert-Accepted { Test-InstallerParameters -Resume -PackageDir $packageRoot -BackupDir $backupRoot }
    Assert-Accepted { Test-InstallerParameters -PackageDir $packageRoot -BackupDir $backupRoot }
    Assert-Accepted { Test-InstallerParameters -Rollback -BackupDir $backupRoot }
    Assert-Rejected { Test-InstallerParameters -Rollback -Resume -PackageDir $packageRoot -BackupDir $backupRoot } `
        "*Parameter set cannot be resolved*" "rollback and resume cannot be combined"
    Assert-Rejected { Test-InstallerParameters -Rollback -PackageDir $packageRoot -BackupDir $backupRoot } `
        "*Parameter set cannot be resolved*" "rollback cannot accept a package"
    Assert-Accepted $checkBackup
    Assert-Accepted $checkPackage
    Assert-Accepted $checkState
} finally {
    # Only remove the exact files created by this test; unexpected files cause
    # non-recursive directory removal to fail instead of broadening deletion.
    foreach ($path in $ownedFiles) {
        if (Test-Path -LiteralPath $path -PathType Leaf) { Remove-Item -LiteralPath $path }
    }
    for ($index = $directories.Count - 1; $index -ge 0; $index--) {
        [IO.Directory]::Delete($directories[$index], $false)
    }
}

Write-Output "All $script:checks installer resume checks passed; no installation was performed."
