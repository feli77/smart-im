<#
.SYNOPSIS
Read-only checks for a complete Smart IM TSF installation.
.DESCRIPTION
Reads process paths, registration, binary feature markers/hashes, the Lua file
hash and the bounded heartbeat timestamp. For the named input applications,
reads only loaded Weasel module metadata and its first 1024 PE-header bytes.
Never reads requests, responses, process heaps,
learning databases, editor contents or any captured document text.
Native feature markers are evidence of a build, not an end-to-end TSF test.
No process is stopped, file written, registry changed or installer launched.
.PARAMETER Processes
Application process names to check for a stale loaded Weasel DLL. Defaults to
Code, notepad, msedge, chrome, QQ and Weixin. Names are exact, without .exe.
Use an empty array to skip loaded-module checks.
#>
[CmdletBinding()]
param(
    [string]$WeaselRoot = "",
    [string]$RimeUserDir = "",
    [string]$NativeBuildDir = "",
    [ValidatePattern('^[\p{L}\p{N}_. -]{1,80}$')]
    [string[]]$Processes = @("Code", "notepad", "msedge", "chrome", "QQ", "Weixin")
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $NativeBuildDir) {
    $NativeBuildDir = Join-Path $projectRoot "artifacts\weasel-smart-im\output"
}

function Read-RegistryValue {
    param([Microsoft.Win32.RegistryHive]$Hive, [Microsoft.Win32.RegistryView]$View,
          [string]$Subkey, [string]$Name)
    $base = $null
    $key = $null
    try {
        $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($Hive, $View)
        $key = $base.OpenSubKey($Subkey, $false)
        if ($key) { return [string]$key.GetValue($Name, "") }
    } catch {
        return ""
    } finally {
        if ($key) { $key.Dispose() }
        if ($base) { $base.Dispose() }
    }
    return ""
}

function Read-BinaryEvidence {
    param([string]$Path, [string]$Role, [string]$ReferenceName = "")
    $result = [ordered]@{ Role = $Role; Path = $Path; Architecture = "unknown";
        Evidence = "missing"; Verified = $false }
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]$result
    }
    try {
        $bytes = [IO.File]::ReadAllBytes($Path)
        if ($bytes.Length -ge 64) {
            $offset = [BitConverter]::ToInt32($bytes, 60)
            if ($offset -ge 0 -and $offset -le $bytes.Length - 6) {
                $machine = [BitConverter]::ToUInt16($bytes, $offset + 4)
                $result.Architecture = switch ($machine) {
                    0x8664 { "x64" }
                    0x014c { "x86" }
                    0xaa64 { "arm64" }
                    default { "unknown" }
                }
            }
        }
        $ascii = [Text.Encoding]::ASCII.GetString($bytes)
        $wide = [Text.Encoding]::Unicode.GetString($bytes)
        if ($wide.Contains("SmartIMContextProtocol")) {
            $result.Evidence = "SmartIMContextProtocol resource marker"
            $result.Verified = $true
        } elseif ($ascii.Contains("smart_im_context") -or $wide.Contains("smart_im_context")) {
            $result.Evidence = "smart_im_context marker"
            $result.Verified = $true
        } elseif ($ascii.Contains("_ReadLocalContext") -or
                  $ascii.Contains("CReadLocalContextSession") -or
                  $ascii.Contains("LocalContext.cpp") -or
                  $wide.Contains("_ReadLocalContext") -or
                  $wide.Contains("CReadLocalContextSession") -or
                  $wide.Contains("LocalContext.cpp")) {
            $result.Evidence = "LocalContext native symbol"
            $result.Verified = $true
        } else {
            $result.Evidence = "no Smart IM TSF marker detected"
            # A release TSF DLL can strip every symbol. The packet prefix 1<TAB>
            # alone is deliberately not accepted as proof of this feature.
            if ($ReferenceName) {
                $reference = Join-Path $NativeBuildDir $ReferenceName
                $buildServer = Join-Path $NativeBuildDir "WeaselServer.exe"
                if ((Test-Path -LiteralPath $reference -PathType Leaf) -and
                    (Test-Path -LiteralPath $buildServer -PathType Leaf)) {
                    $serverBytes = [IO.File]::ReadAllBytes($buildServer)
                    $patchedBuild = [Text.Encoding]::ASCII.GetString($serverBytes).Contains("smart_im_context")
                    if ($patchedBuild -and
                        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq
                        (Get-FileHash -LiteralPath $reference -Algorithm SHA256).Hash) {
                        $result.Evidence = "SHA256 matches local patched build"
                        $result.Verified = $true
                    }
                }
            }
        }
    } catch {
        # Exception text could include incidental file data; report only its type.
        $result.Evidence = "unreadable: " + $_.Exception.GetType().Name
    }
    return [pscustomobject]$result
}

function Read-PeHeaderIdentity {
    param([byte[]]$Bytes)
    if ($Bytes.Length -lt 64 -or [BitConverter]::ToUInt16($Bytes, 0) -ne 0x5a4d) {
        throw "Invalid DOS header."
    }
    $offset = [BitConverter]::ToInt32($Bytes, 60)
    if ($offset -lt 64 -or $offset -gt $Bytes.Length - 84 -or
        [BitConverter]::ToUInt32($Bytes, $offset) -ne 0x00004550 -or
        [BitConverter]::ToUInt16($Bytes, $offset + 20) -lt 60 -or
        [BitConverter]::ToUInt16($Bytes, $offset + 24) -notin @(0x010b, 0x020b)) {
        throw "Invalid or unsupported PE header."
    }
    return [pscustomobject]@{
        Machine = [BitConverter]::ToUInt16($Bytes, $offset + 4)
        TimeDateStamp = [BitConverter]::ToUInt32($Bytes, $offset + 8)
        SizeOfImage = [BitConverter]::ToUInt32($Bytes, $offset + 80)
    }
}

function Read-LoadedTsfEvidence {
    param([string[]]$ProcessNames, [string]$InstallRoot)
    if (-not $ProcessNames -or -not $InstallRoot) { return }
    if ($ProcessNames.Count -gt 32) { throw "At most 32 application process names may be checked." }
    if (-not ("SmartIM.ReadOnlyModuleHeader" -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace SmartIM {
    public static class ReadOnlyModuleHeader {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inherit, int processId);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool ReadProcessMemory(IntPtr process, IntPtr address,
            [Out] byte[] buffer, UIntPtr size, out UIntPtr bytesRead);
        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);
        public static byte[] Read(int processId, IntPtr moduleBase) {
            // PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ. Never request
            // write, injection, suspend or termination rights; read only one header.
            IntPtr process = OpenProcess(0x1010, false, processId);
            if (process == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
            try {
                byte[] header = new byte[1024];
                UIntPtr count;
                if (!ReadProcessMemory(process, moduleBase, header,
                    new UIntPtr(1024), out count))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                if (count.ToUInt64() != 1024) throw new InvalidOperationException("Incomplete PE header.");
                return header;
            } finally { CloseHandle(process); }
        }
    }
}
'@
    }
    $referenceHeaders = @{}
    foreach ($entry in @(@(0x8664, "weaselx64.dll"), @(0x014c, "weasel.dll"))) {
        $stream = $null
        try {
            $stream = [IO.File]::OpenRead((Join-Path $InstallRoot $entry[1]))
            $bytes = New-Object byte[] 1024
            $count = $stream.Read($bytes, 0, $bytes.Length)
            if ($count -ne $bytes.Length) { throw "Incomplete file PE header." }
            $identity = Read-PeHeaderIdentity $bytes
            if ($identity.Machine -ne $entry[0]) { throw "Unexpected reference architecture." }
            $referenceHeaders[[int]$entry[0]] = $identity
        } catch {
            # Missing/unreadable disk files are reported by the existing checks.
        } finally { if ($stream) { $stream.Dispose() } }
    }
    foreach ($application in @(Get-Process -Name ($ProcessNames | Select-Object -Unique) -ErrorAction SilentlyContinue)) {
        try {
            $modules = @($application.Modules | Where-Object {
                $_.ModuleName -match '^weasel(?:x64)?\.dll(?:\.(?:old\.\d+|smart-im-old-[0-9a-f]+))?$'
            })
            foreach ($module in $modules) {
                try {
                    $identity = Read-PeHeaderIdentity ([SmartIM.ReadOnlyModuleHeader]::Read($application.Id, $module.BaseAddress))
                    $reference = $referenceHeaders[[int]$identity.Machine]
                    $status = "unavailable"
                    if ($reference) {
                        $status = if ($identity.TimeDateStamp -eq $reference.TimeDateStamp -and
                            $identity.SizeOfImage -eq $reference.SizeOfImage) { "matching" } else { "stale" }
                    }
                } catch {
                    $status = "access denied or unavailable"
                }
                [pscustomobject]@{ Name = $application.ProcessName; Id = $application.Id; Status = $status }
            }
        } catch {
            [pscustomobject]@{ Name = $application.ProcessName; Id = $application.Id;
                Status = "access denied or unavailable" }
        }
    }
}

Write-Output "Smart IM TSF doctor (read-only; no document text is read)"
Write-Output "Disk markers and loaded PE-header identities are checked separately; matching headers are not an end-to-end TSF test."
$serverPaths = @()
foreach ($process in @(Get-Process -Name WeaselServer -ErrorAction SilentlyContinue)) {
    try { $processPath = $process.Path } catch { $processPath = "" }
    if ($processPath) {
        $serverPaths += $processPath
        Write-Output ("Running WeaselServer PID {0}: {1}" -f $process.Id, $processPath)
    } else {
        Write-Output ("Running WeaselServer PID {0}: path unavailable" -f $process.Id)
    }
}
if ($serverPaths.Count -eq 0) {
    Write-Output "Running WeaselServer: no readable running server path"
}

$registryRoot = Read-RegistryValue LocalMachine Registry32 "Software\Rime\Weasel" "WeaselRoot"
if (-not $registryRoot) {
    $registryRoot = Read-RegistryValue LocalMachine Registry64 "Software\Rime\Weasel" "WeaselRoot"
}
Write-Output ("Registered WeaselRoot: {0}" -f $(if ($registryRoot) { $registryRoot } else { "not found" }))
if (-not $WeaselRoot) {
    if ($serverPaths.Count -gt 0) { $WeaselRoot = Split-Path -Parent $serverPaths[0] }
    else { $WeaselRoot = $registryRoot }
}
if (-not $RimeUserDir) {
    $RimeUserDir = Read-RegistryValue CurrentUser Registry32 "Software\Rime\Weasel" "RimeUserDir"
    if (-not $RimeUserDir) { $RimeUserDir = Join-Path $env:APPDATA "Rime" }
}
Write-Output ("Rime user directory: {0}" -f $RimeUserDir)

$checks = @()
foreach ($serverPath in @($serverPaths | Select-Object -Unique)) {
    $checks += Read-BinaryEvidence $serverPath "running server"
}
if ($WeaselRoot) {
    $checks += Read-BinaryEvidence (Join-Path $WeaselRoot "WeaselServer.exe") "installed server"
    $checks += Read-BinaryEvidence (Join-Path $WeaselRoot "weaselx64.dll") "installed TSF x64" "weaselx64.dll"
    $checks += Read-BinaryEvidence (Join-Path $WeaselRoot "weasel.dll") "installed TSF x86" "weasel.dll"
}
$classKey = "CLSID\{A3F4CDED-B1E9-41EE-9CA6-7B4D0DE6CB0A}\InprocServer32"
foreach ($architecture in @("x64", "x86")) {
    $view = if ($architecture -eq "x64") { "Registry64" } else { "Registry32" }
    $registeredDll = Read-RegistryValue ClassesRoot $view $classKey ""
    $referenceName = if ($architecture -eq "x64") { "weaselx64.dll" } else { "weasel.dll" }
    $checks += Read-BinaryEvidence $registeredDll "registered TSF $architecture" $referenceName
}
foreach ($check in $checks) {
    Write-Output ("[{0}; {1}] {2}: {3}" -f $check.Role, $check.Architecture, $check.Evidence, $check.Path)
}

$loadedModules = @(Read-LoadedTsfEvidence -ProcessNames $Processes -InstallRoot $WeaselRoot)
$staleModules = @($loadedModules | Where-Object { $_.Status -eq "stale" })
foreach ($module in $loadedModules) {
    Write-Output ("[loaded TSF] {0} PID {1}: {2}" -f $module.Name, $module.Id, $module.Status)
}
if ($loadedModules.Count -eq 0) {
    Write-Output "Loaded TSF: no eligible module found in the selected applications."
}
if ($staleModules.Count -gt 0) {
    Write-Output "ACTION: completely exit and reopen the applications marked stale; their loaded Weasel DLL differs from the installed DLL. Save work before signing out/in if needed."
}

$repoLua = Join-Path $projectRoot "src\smart_im\rime_assets\lua\smart_im.lua"
$installedLua = Join-Path $RimeUserDir "lua\smart_im.lua"
$luaMatches = $false
try {
    if ((Test-Path -LiteralPath $installedLua -PathType Leaf) -and
        (Test-Path -LiteralPath $repoLua -PathType Leaf)) {
        $luaMatches = (Get-FileHash -LiteralPath $installedLua -Algorithm SHA256).Hash -eq
                      (Get-FileHash -LiteralPath $repoLua -Algorithm SHA256).Hash
    }
    Write-Output ("Lua matches this repository: {0} ({1})" -f $luaMatches, $installedLua)
} catch {
    Write-Output "Lua comparison unavailable"
}

$heartbeat = Join-Path $RimeUserDir "smart_im_runtime\heartbeat"
$heartbeatLive = $false
$stream = $null
try {
    if (Test-Path -LiteralPath $heartbeat -PathType Leaf) {
        $stream = [IO.File]::Open($heartbeat, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                                 [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
        $buffer = New-Object byte[] 33
        $count = $stream.Read($buffer, 0, $buffer.Length)
        $value = [Text.Encoding]::ASCII.GetString($buffer, 0, $count)
        $epoch = 0L
        if ($count -le 32 -and $value -match '^\d+\r?\n?$' -and
            [long]::TryParse($value.Trim(), [ref]$epoch)) {
            $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $epoch
            $heartbeatLive = $age -ge 0 -and $age -le 3
            Write-Output ("Smart IM heartbeat live: {0}; age {1} seconds" -f $heartbeatLive, $age)
        } else {
            Write-Output "Smart IM heartbeat: invalid timestamp/framing"
        }
    } else {
        Write-Output "Smart IM heartbeat: missing"
    }
} catch {
    Write-Output "Smart IM heartbeat: unavailable"
} finally {
    if ($stream) { $stream.Dispose() }
}

$nativeVerified = $checks.Count -ge 5 -and @($checks | Where-Object { -not $_.Verified }).Count -eq 0
if (-not $nativeVerified) {
    Write-Output "RESULT: the complete patched native TSF/server installation is NOT verified."
    Write-Output "Updating smart_im.lua alone cannot supply TSF context. Use install_weasel_tsf.ps1 for the matching server and BOTH system TSF DLLs; use -Resume with the original backup if a previous installation stopped after copying files."
    Write-Output "After installation, restart the server and the applications that loaded the old TSF DLL (sign out/in if needed), then rerun this script. This script performs no installation or restart."
} else {
    Write-Output "Native binaries have Smart IM evidence. This does not prove a particular editor supports TSF surrounding text."
}
if (-not $luaMatches) { Write-Output "ACTION: update the deployed smart_im.lua from this repository and redeploy Rime." }
if (-not $heartbeatLive) { Write-Output "ACTION: start/check the Smart IM reranking service; a live heartbeat is required." }
if (-not $nativeVerified -or -not $luaMatches -or -not $heartbeatLive -or $serverPaths.Count -eq 0 -or
    $staleModules.Count -gt 0) {
    exit 1
}
exit 0
