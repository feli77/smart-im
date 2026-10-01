<#
.SYNOPSIS
Read-only checks for a complete Smart IM TSF installation.
.DESCRIPTION
Reads process paths, registration, binary feature markers/hashes, the Lua file
hash and the bounded heartbeat timestamp. Never reads requests, responses,
learning databases, editor contents or any captured document text.
Native feature markers are evidence of a build, not an end-to-end TSF test.
No process is stopped, file written, registry changed or installer launched.
#>
[CmdletBinding()]
param(
    [string]$WeaselRoot = "",
    [string]$RimeUserDir = "",
    [string]$NativeBuildDir = ""
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

Write-Output "Smart IM TSF doctor (read-only; no document text is read)"
Write-Output "Binary evidence describes files on disk; already loaded modules require an application/server restart."
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
if (-not $nativeVerified -or -not $luaMatches -or -not $heartbeatLive -or $serverPaths.Count -eq 0) {
    exit 1
}
exit 0
