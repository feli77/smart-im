<#
.SYNOPSIS
Install, resume or roll back the tested Smart IM Weasel native binaries.
.DESCRIPTION
Run explicitly from an administrator, 64-bit PowerShell on x64 Windows.
Supports an existing simplified-Chinese (Hant=0) Weasel installation only.
The package manifest.json must contain files mapping the three fixed filenames
to their SHA256 hashes. Backups are completed before the server is stopped.
The existing TSF CLSID and registered paths must already be correct. This
binary-only update replaces the two system DLLs without rerunning WeaselSetup.
No user dictionary, Lua file, startup entry or user-directory setting is changed.
Registry exports are recovery evidence; no input profile is re-registered.
The server remains stopped so it can be restarted from a normal user session.
.EXAMPLE
.\scripts\install_weasel_tsf.ps1 -PackageDir C:\tested-package -BackupDir C:\weasel-backup-unique
.EXAMPLE
.\scripts\install_weasel_tsf.ps1 -Rollback -BackupDir C:\weasel-backup-unique
.EXAMPLE
.\scripts\install_weasel_tsf.ps1 -Resume -PackageDir C:\tested-package -BackupDir C:\weasel-backup-unique
#>
[CmdletBinding(DefaultParameterSetName = "Install")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Install")]
    [Parameter(Mandatory = $true, ParameterSetName = "Resume")]
    [string]$PackageDir,
    [Parameter(Mandatory = $true)]
    [string]$BackupDir,
    [Parameter(Mandatory = $true, ParameterSetName = "Rollback")]
    [switch]$Rollback,
    [Parameter(Mandatory = $true, ParameterSetName = "Resume")]
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
    throw "Use 64-bit PowerShell on x64 Windows. Other architectures are not supported by this installer."
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script explicitly in an administrator PowerShell window. No changes were made."
}

$names = @("WeaselServer.exe", "weaselx64.dll", "weasel.dll")
$classKey = "CLSID\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}\InprocServer32"
$werKey = "Software\Microsoft\Windows\Windows Error Reporting\LocalDumps\WeaselServer.exe"

function Read-Reg {
    param([Microsoft.Win32.RegistryHive]$Hive, [Microsoft.Win32.RegistryView]$View,
          [string]$Subkey, [string]$Name)
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, $View)
    $key = $base.OpenSubKey($Subkey, $false)
    try { if ($key) { return $key.GetValue($Name, $null) } }
    finally { if ($key) { $key.Dispose() }; $base.Dispose() }
    return $null
}

function Hash-File {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Write-InstallStatus {
    param([string]$Message)
    # Persist progress before console output, which classic QuickEdit can pause.
    if ($script:InstallerLogPath) {
        [IO.File]::AppendAllText($script:InstallerLogPath,
            ([DateTimeOffset]::Now.ToString("o") + " " + $Message + [Environment]::NewLine))
    }
    Write-Host $Message
}

function Check-Hash {
    param([string]$Path, [string]$Expected)
    if ($Expected -notmatch '^[0-9a-fA-F]{64}$' -or (Hash-File $Path) -ne $Expected) {
        throw "SHA256 mismatch: $Path"
    }
}

function Check-PackageBinary {
    param([string]$Path, [string]$Name)
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 64) { throw "Invalid PE file: $Name" }
    $offset = [BitConverter]::ToInt32($bytes, 60)
    if ($offset -lt 0 -or $offset -gt $bytes.Length - 6 -or
        [BitConverter]::ToUInt32($bytes, $offset) -ne 0x00004550) {
        throw "Invalid PE header: $Name"
    }
    $expectedMachine = if ($Name -eq "weasel.dll") { 0x014c } else { 0x8664 }
    if ([BitConverter]::ToUInt16($bytes, $offset + 4) -ne $expectedMachine) {
        throw "Wrong binary architecture: $Name"
    }
    if ($Name -eq "WeaselServer.exe") {
        $marked = [Text.Encoding]::ASCII.GetString($bytes).Contains("smart_im_context")
    } else {
        $marked = [Text.Encoding]::Unicode.GetString($bytes).Contains("SmartIMContextProtocol")
    }
    if (-not $marked) { throw "Required Smart IM native feature marker is missing: $Name" }
}

function Read-VerifiedPackage {
    param([string]$SourceDir)
    $package = Get-Content -LiteralPath (Join-Path $SourceDir "manifest.json") -Raw | ConvertFrom-Json
    if (@($package.files.PSObject.Properties).Count -ne 3) {
        throw "Package must declare exactly the three native files."
    }
    foreach ($name in @("WeaselServer.exe", "weaselx64.dll", "weasel.dll")) {
        Check-Hash (Join-Path $SourceDir $name) $package.files.$name
        Check-PackageBinary (Join-Path $SourceDir $name) $name
    }
    return $package
}

function Read-VerifiedBackup {
    param([string]$Directory, [string]$InstallRoot, [string]$SetupPath)
    $saved = Get-Content -LiteralPath (Join-Path $Directory "backup.json") -Raw | ConvertFrom-Json
    if ($saved.kind -ne "smart-im-weasel-tsf-backup" -or $saved.version -ne 1 -or
        $saved.complete -isnot [bool] -or $saved.complete -ne $true -or
        $saved.install_root -ne $InstallRoot -or $saved.hant -ne 0) {
        throw "Backup is incomplete or belongs to a different installation."
    }
    if (@($saved.files.PSObject.Properties).Count -ne 3 -or
        @($saved.system_files.PSObject.Properties).Count -ne 2) {
        throw "Backup must declare exactly three installed files and two system DLLs."
    }
    Check-Hash $SetupPath $saved.setup_sha256
    Check-Hash (Join-Path $Directory "WeaselSetup.exe") $saved.setup_sha256
    foreach ($name in @("WeaselServer.exe", "weaselx64.dll", "weasel.dll")) {
        Check-Hash (Join-Path (Join-Path $Directory "installed") $name) $saved.files.$name
    }
    foreach ($name in @("weaselx64.dll", "weasel.dll")) {
        Check-Hash (Join-Path (Join-Path $Directory "system") $name) $saved.system_files.$name
        if ($saved.system_files.$name -ne $saved.files.$name) {
            throw "Original system DLLs differ from source DLLs; this backup requires manual recovery."
        }
    }
    return $saved
}

function Check-ResumeState {
    param([string]$InstallRoot, [hashtable]$SystemDlls, $OriginalHashes, $TargetHashes)
    foreach ($name in @("WeaselServer.exe", "weaselx64.dll", "weasel.dll")) {
        $path = Join-Path $InstallRoot $name
        $currentHash = Hash-File $path
        if ($currentHash -ne $OriginalHashes.$name -and $currentHash -ne $TargetHashes.$name) {
            throw "Resume refused: installed file is neither the backed-up original nor the verified package: $path"
        }
    }
    foreach ($name in @("weaselx64.dll", "weasel.dll")) {
        $path = $SystemDlls[$name]
        $currentHash = Hash-File $path
        if ($currentHash -ne $OriginalHashes.$name -and $currentHash -ne $TargetHashes.$name) {
            throw "Resume refused: system DLL is neither the backed-up original nor the verified package: $path"
        }
    }
}

function Invoke-InstallerProcess {
    param([string]$FilePath, [string[]]$ArgumentList,
          [int]$TimeoutSeconds, [string]$Stage)
    if ($TimeoutSeconds -le 0 -or $TimeoutSeconds -gt 300) {
        throw "Invalid process timeout for $Stage."
    }
    $process = $null
    $terminator = $null
    try {
        # Start-Process -Wait waits without a deadline, including when /q gets
        # stuck in the old server's synchronous named-pipe shutdown protocol.
        $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
            -WindowStyle Hidden -PassThru
        $null = $process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            # Only target this invocation's still-running process and its
            # descendants; never terminate processes by executable name.
            if (-not $process.HasExited) {
                $terminator = Start-Process -FilePath (Join-Path $env:WINDIR "System32\taskkill.exe") `
                    -ArgumentList @("/PID", [string]$process.Id, "/T", "/F") -WindowStyle Hidden -PassThru
                $null = $terminator.Handle
                if (-not $terminator.WaitForExit(5000)) {
                    $terminator.Kill()
                    throw "$Stage timed out; process-tree cleanup for PID $($process.Id) could not be confirmed."
                }
                if ($terminator.ExitCode -ne 0) {
                    throw "$Stage timed out; process-tree cleanup for PID $($process.Id) returned $($terminator.ExitCode)."
                }
                if (-not $process.WaitForExit(5000)) {
                    throw "$Stage timed out and process $($process.Id) could not be stopped."
                }
            }
            throw "$Stage timed out after $TimeoutSeconds seconds; its process tree was stopped."
        }
        return $process.ExitCode
    } finally {
        if ($terminator) { $terminator.Dispose() }
        if ($process) { $process.Dispose() }
    }
}

function Get-InstalledServerProcess {
    param([string]$ServerPath)
    $sessionId = (Get-Process -Id $PID).SessionId
    foreach ($process in @(Get-Process -Name WeaselServer -ErrorAction SilentlyContinue)) {
        if ($process.SessionId -ne $sessionId) {
            throw "WeaselServer is active in another Windows session; sign out that session before replacing shared binaries."
        }
        if (-not $process.Path -or $process.Path -ne $ServerPath) {
            throw "A different or unreadable WeaselServer instance is active; inspect doctor_tsf.ps1 first."
        }
        $process
    }
}

function Stop-InstalledServer {
    param([string]$ServerPath)
    if (@(Get-InstalledServerProcess $ServerPath).Count -eq 0) {
        Write-InstallStatus "[1/4] No running WeaselServer; skipping /q."
        return
    }
    Write-InstallStatus "[1/4] Requesting WeaselServer shutdown (maximum 10 seconds)..."
    try {
        $quitCode = Invoke-InstallerProcess -FilePath $ServerPath -ArgumentList @("/q") `
            -TimeoutSeconds 10 -Stage "WeaselServer /q"
        if ($quitCode -ne 0) { Write-Warning "WeaselServer /q returned $quitCode." }
    } catch {
        Write-Warning $_.Exception.Message
    }
    # The shutdown reply precedes the server's dictionary finalization. Give
    # it a separate, bounded grace period before considering a forced stop.
    Write-InstallStatus "[1/4] Waiting up to 10 seconds for server finalization..."
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        if (@(Get-InstalledServerProcess $ServerPath).Count -eq 0) { return }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    # A previous interrupted installation can leave its /q helper alive even
    # after the real server has exited. Revalidate path and session before
    # stopping each remaining process; never kill by image name alone.
    foreach ($process in @(Get-InstalledServerProcess $ServerPath)) {
        if ($process.HasExited) { continue }
        Write-Warning "Stopping remaining WeaselServer PID $($process.Id) before replacing files."
        Stop-Process -InputObject $process -Force
        if (-not $process.WaitForExit(5000)) {
            throw "WeaselServer PID $($process.Id) did not exit. No binary was replaced."
        }
    }
    if (@(Get-InstalledServerProcess $ServerPath).Count -gt 0) {
        throw "WeaselServer restarted during installation. No binary was replaced."
    }
}

function Set-TsfSystemBinary {
    param([string]$Source, [string]$Destination, [string]$ExpectedHash)
    $sourcePath = [IO.Path]::GetFullPath($Source)
    $destinationPath = [IO.Path]::GetFullPath($Destination)
    if ($sourcePath -eq $destinationPath) {
        throw "Source and destination must be different files."
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $destinationPath -PathType Leaf)) {
        throw "Both the source and existing TSF destination must be files."
    }
    Check-Hash $sourcePath $ExpectedHash
    if ((Hash-File $destinationPath) -eq $ExpectedHash) {
        return [pscustomobject]@{ Destination = $destinationPath; Changed = $false;
            RetainedOldPath = $null; CleanupAtReboot = $false }
    }

    $suffix = [guid]::NewGuid().ToString("N")
    $stagePath = "$destinationPath.smart-im-stage-$suffix"
    $oldPath = "$destinationPath.smart-im-old-$suffix"
    $oldHash = Hash-File $destinationPath
    $oldMoved = $false
    $newMoved = $false
    try {
        # Complete and verify the copy before touching the registered DLL.
        # A loaded Windows image can be renamed even when it cannot be
        # overwritten. Both renames stay on the destination's volume.
        Copy-Item -LiteralPath $sourcePath -Destination $stagePath -ErrorAction Stop
        Check-Hash $stagePath $ExpectedHash
        Move-Item -LiteralPath $destinationPath -Destination $oldPath -ErrorAction Stop
        $oldMoved = $true
        Move-Item -LiteralPath $stagePath -Destination $destinationPath -ErrorAction Stop
        $newMoved = $true
        Check-Hash $destinationPath $ExpectedHash
    } catch {
        $replaceFailure = $_.Exception.Message
        if ($oldMoved) {
            try {
                if ($newMoved) {
                    # The new DLL may already be loaded by another process.
                    # Rename it aside before restoring the original as well.
                    Move-Item -LiteralPath $destinationPath -Destination $stagePath -ErrorAction Stop
                }
                Move-Item -LiteralPath $oldPath -Destination $destinationPath -ErrorAction Stop
                $oldMoved = $false
                Check-Hash $destinationPath $oldHash
            } catch {
                throw "TSF replacement failed ($replaceFailure); restoring $destinationPath also failed: $($_.Exception.Message). Preserve $oldPath and the installation backup for recovery."
            }
        }
        throw "TSF replacement failed; the original destination was preserved: $replaceFailure"
    } finally {
        if (Test-Path -LiteralPath $stagePath -PathType Leaf) {
            try { Remove-Item -LiteralPath $stagePath -Force -ErrorAction Stop }
            catch { Write-Warning "Could not remove staged file: $stagePath" }
        }
    }

    $retainedOldPath = $null
    $cleanupAtReboot = $false
    try { Remove-Item -LiteralPath $oldPath -Force -ErrorAction Stop }
    catch {
        $retainedOldPath = $oldPath
        # Cleanup must not turn a verified replacement into an install failure.
        # MOVEFILE_DELAY_UNTIL_REBOOT requires administrator privileges.
        try {
            $cleanupIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
            try {
                $cleanupPrincipal = New-Object Security.Principal.WindowsPrincipal($cleanupIdentity)
                $isAdministrator = $cleanupPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
            } finally { $cleanupIdentity.Dispose() }
            if ($isAdministrator) {
                if (-not ("SmartIM.NativeFileCleanup" -as [type])) {
                    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
namespace SmartIM {
    public static class NativeFileCleanup {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool MoveFileExW(string existingPath, string newPath, uint flags);
    }
}
'@
                }
                $cleanupAtReboot = [SmartIM.NativeFileCleanup]::MoveFileExW($oldPath, $null, 4)
            }
        } catch { Write-Warning "Could not schedule old DLL cleanup: $($_.Exception.Message)" }
        if ($cleanupAtReboot) {
            Write-Warning "The previous loaded DLL will be removed at reboot: $oldPath"
        } else {
            Write-Warning "The previous DLL remains at $oldPath; remove it after applications exit or Windows restarts."
        }
    }
    return [pscustomobject]@{ Destination = $destinationPath; Changed = $true;
        RetainedOldPath = $retainedOldPath; CleanupAtReboot = $cleanupAtReboot }
}

function Read-WerValues {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey("LocalMachine", "Registry64")
    $key = $base.OpenSubKey($werKey, $false)
    try {
        foreach ($name in @("DumpFolder", "DumpType", "CustomDumpFlags", "DumpCount")) {
            $present = $key -and $key.GetValueNames() -contains $name
            $kind = $null
            $value = $null
            if ($present) {
                $kind = [string]$key.GetValueKind($name)
                $value = $key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
            }
            [pscustomobject]@{ Name = $name; Present = [bool]$present; Kind = $kind; Value = $value }
        }
    } finally { if ($key) { $key.Dispose() }; $base.Dispose() }
}

$installRoot = [string](Read-Reg LocalMachine Registry32 "Software\Rime\Weasel" "WeaselRoot")
if (-not $installRoot) { throw "No existing Weasel installation was found in the 32-bit registry view." }
$installRoot = (Resolve-Path -LiteralPath $installRoot).Path
$hant = Read-Reg CurrentUser Registry32 "Software\Rime\Weasel" "Hant"
if ($null -eq $hant -or [int]$hant -ne 0) {
    throw "This script only supports an existing simplified-Chinese installation with Hant=0."
}
$serverExecutable = Read-Reg LocalMachine Registry32 "Software\Rime\Weasel" "ServerExecutable"
if ($serverExecutable -ne "WeaselServer.exe") { throw "Unexpected existing server executable setting." }
$setup = Join-Path $installRoot "WeaselSetup.exe"
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) { throw "Existing official WeaselSetup.exe is required." }
$setupBytes = [IO.File]::ReadAllBytes($setup)
if ($setupBytes.Length -lt 64) { throw "Invalid existing WeaselSetup.exe PE file." }
$setupPe = [BitConverter]::ToInt32($setupBytes, 60)
if ($setupPe -lt 0 -or $setupPe -gt $setupBytes.Length - 6 -or
    [BitConverter]::ToUInt32($setupBytes, $setupPe) -ne 0x00004550 -or
    [BitConverter]::ToUInt16($setupBytes, $setupPe + 4) -ne 0x014c) {
    throw "The installed WeaselSetup.exe must be the official x86 setup for this installation."
}
$systemDlls = @{
    "weaselx64.dll" = Join-Path $env:WINDIR "System32\weasel.dll"
    "weasel.dll" = Join-Path $env:WINDIR "SysWOW64\weasel.dll"
}
foreach ($architecture in @("x64", "x86")) {
    $view = if ($architecture -eq "x64") { "Registry64" } else { "Registry32" }
    $name = if ($architecture -eq "x64") { "weaselx64.dll" } else { "weasel.dll" }
    $registered = [string](Read-Reg ClassesRoot $view $classKey "")
    if (-not $registered -or [IO.Path]::GetFullPath($registered) -ne $systemDlls[$name]) {
        throw "Unexpected registered $architecture TSF path. This binary-only updater requires valid existing registration."
    }
}
$BackupDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BackupDir)
$backupManifest = Join-Path $BackupDir "backup.json"
$server = Join-Path $installRoot "WeaselServer.exe"
$null = @(Get-InstalledServerProcess $server)

if ($Rollback) {
    $saved = Read-VerifiedBackup $BackupDir $installRoot $setup
    $sourceDir = Join-Path $BackupDir "installed"
    $targetHashes = $saved.files
} elseif ($Resume) {
    $saved = Read-VerifiedBackup $BackupDir $installRoot $setup
    $sourceDir = (Resolve-Path -LiteralPath $PackageDir).Path
    $package = Read-VerifiedPackage $sourceDir
    $targetHashes = $package.files
    Check-ResumeState $installRoot $systemDlls $saved.files $targetHashes
} else {
    $sourceDir = (Resolve-Path -LiteralPath $PackageDir).Path
    $package = Read-VerifiedPackage $sourceDir
    if (Test-Path -LiteralPath $BackupDir) { throw "BackupDir must not already exist. Choose a unique persistent directory." }
    $userDir = [string](Read-Reg CurrentUser Registry32 "Software\Rime\Weasel" "RimeUserDir")
    if (-not $userDir) { $userDir = Join-Path $env:APPDATA "Rime" }
    foreach ($protected in @($installRoot, $env:WINDIR, $userDir, $sourceDir)) {
        $root = [IO.Path]::GetFullPath($protected).TrimEnd([IO.Path]::DirectorySeparatorChar)
        if ($BackupDir -eq $root -or $BackupDir.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "BackupDir must be outside the installation, Windows, Rime user data and package directories."
        }
    }
    # A source/system mismatch needs a custom recovery plan; do not guess how to
    # replace an already-loaded system DLL in an unrelated installation state.
    foreach ($name in $systemDlls.Keys) {
        if ((Hash-File $systemDlls[$name]) -ne (Hash-File (Join-Path $installRoot $name))) {
            throw "Existing source/system DLLs differ. Repair or review this state before installing."
        }
    }
    New-Item -ItemType Directory -Path $BackupDir | Out-Null
    foreach ($directory in @("installed", "system", "registry")) {
        New-Item -ItemType Directory -Path (Join-Path $BackupDir $directory) | Out-Null
    }
    $originalHashes = [ordered]@{}
    $systemHashes = [ordered]@{}
    foreach ($name in $names) {
        $original = Join-Path $installRoot $name
        $destination = Join-Path (Join-Path $BackupDir "installed") $name
        Copy-Item -LiteralPath $original -Destination $destination
        $originalHashes[$name] = Hash-File $original
        Check-Hash $destination $originalHashes[$name]
    }
    foreach ($name in $systemDlls.Keys) {
        $destination = Join-Path (Join-Path $BackupDir "system") $name
        Copy-Item -LiteralPath $systemDlls[$name] -Destination $destination
        $systemHashes[$name] = Hash-File $systemDlls[$name]
        Check-Hash $destination $systemHashes[$name]
    }
    Copy-Item -LiteralPath $setup -Destination (Join-Path $BackupDir "WeaselSetup.exe")
    $registryKeys = @(
        "HKLM\SOFTWARE\WOW6432Node\Rime\Weasel",
        "HKCU\Software\Rime\Weasel",
        "HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\WeaselServer.exe",
        "HKLM\SOFTWARE\Classes\CLSID\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}",
        "HKLM\SOFTWARE\Classes\WOW6432Node\CLSID\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}",
        "HKLM\SOFTWARE\Microsoft\CTF\TIP\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}",
        "HKLM\SOFTWARE\WOW6432Node\Microsoft\CTF\TIP\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}",
        "HKCU\Software\Microsoft\CTF", "HKCU\Keyboard Layout",
        "HKCU\Control Panel\International\User Profile"
    )
    $exports = @()
    $number = 0
    foreach ($key in $registryKeys) {
        $number++
        $providerKey = $key.Replace("HKLM\", "Registry::HKEY_LOCAL_MACHINE\").Replace("HKCU\", "Registry::HKEY_CURRENT_USER\")
        $exists = Test-Path -LiteralPath $providerKey
        $filename = "registry\{0:D2}.reg" -f $number
        if ($exists) {
            & (Join-Path $env:WINDIR "System32\reg.exe") export $key (Join-Path $BackupDir $filename) /y | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "Failed to export registry backup: $key" }
        }
        $exports += [pscustomobject]@{ key = $key; existed = $exists; file = $filename }
    }
    $saved = [pscustomobject]@{
        kind = "smart-im-weasel-tsf-backup"; version = 1; complete = $true
        created_utc = [DateTimeOffset]::UtcNow.ToString("o"); install_root = $installRoot; hant = 0
        setup_sha256 = Hash-File $setup; files = $originalHashes; system_files = $systemHashes
        wer_values = @(Read-WerValues); registry_exports = $exports
    }
    $saved | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $backupManifest -Encoding UTF8
    $targetHashes = $package.files
}

$script:InstallerLogPath = Join-Path $BackupDir "install.log"
Write-InstallStatus ("Backup ready: {0}" -f $BackupDir)
Write-InstallStatus ("{0}: {1}" -f $(if ($Rollback) { "Rolling back" } elseif ($Resume) { "Resuming" } else { "Installing" }), $installRoot)
try {
    Stop-InstalledServer $server
    Write-InstallStatus "[2/4] Copying the three native binaries..."
    foreach ($name in $names) {
        Check-Hash (Join-Path $sourceDir $name) $targetHashes.$name
        Copy-Item -LiteralPath (Join-Path $sourceDir $name) -Destination (Join-Path $installRoot $name) -Force
    }
    Write-InstallStatus "[3/4] Updating system DLLs at their existing registered paths..."
    foreach ($name in @("weasel.dll", "weaselx64.dll")) {
        Write-InstallStatus ("Updating {0}" -f $systemDlls[$name])
        $replacement = Set-TsfSystemBinary -Source (Join-Path $sourceDir $name) `
            -Destination $systemDlls[$name] -ExpectedHash $targetHashes.$name
        Write-InstallStatus ("Verified {0}" -f $systemDlls[$name])
        if ($replacement.RetainedOldPath) {
            Write-InstallStatus ("Previous DLL: {0}; cleanup at reboot: {1}" -f $replacement.RetainedOldPath, $replacement.CleanupAtReboot)
        }
    }
    Write-InstallStatus "[4/4] Verifying installed files and existing TSF registrations..."
    foreach ($name in $names) { Check-Hash (Join-Path $installRoot $name) $targetHashes.$name }
    foreach ($name in $systemDlls.Keys) { Check-Hash $systemDlls[$name] $targetHashes.$name }
    foreach ($architecture in @("x64", "x86")) {
        $view = if ($architecture -eq "x64") { "Registry64" } else { "Registry32" }
        $name = if ($architecture -eq "x64") { "weaselx64.dll" } else { "weasel.dll" }
        if ((Read-Reg ClassesRoot $view $classKey "") -ne $systemDlls[$name]) {
            throw "The $architecture TSF registration did not resolve to the expected system DLL."
        }
    }
} catch {
    Write-InstallStatus ("ERROR: " + $_.Exception.Message)
    Write-InstallStatus "Native update did not complete; the persistent backup has been kept."
    Write-InstallStatus ("Recovery command: & '{0}' -Rollback -BackupDir '{1}'" -f $PSCommandPath.Replace("'", "''"), $BackupDir.Replace("'", "''"))
    throw
}
Write-InstallStatus "Native files and both system DLL hashes/registration paths verified."
Write-Output "Close and reopen applications that loaded the old TSF DLL. Save work before signing out/in if needed."
Write-Output "Open a NORMAL (non-administrator) PowerShell window and start the server there:"
Write-Output ("Start-Process -FilePath '{0}' -WindowStyle Hidden" -f $server.Replace("'", "''"))
Write-Output "Then run scripts/doctor_tsf.ps1 and test an editor. No server was started by this elevated script."
