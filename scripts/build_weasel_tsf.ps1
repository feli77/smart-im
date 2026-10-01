<#
.SYNOPSIS
Build the Smart IM Weasel fork and package its native Windows binaries.
.DESCRIPTION
Requires the patched Weasel 0.17.4 checkout, complete Boost 1.84.0 sources,
the official librime 1.17.0 MSVC x64/x86 SDKs, and Visual Studio 2022 C++
tools with ATL and a Windows SDK. SDK paths may name the extracted
archive directory or its dist directory. Dependencies are never downloaded.
The package contains only WeaselServer.exe, weaselx64.dll, weasel.dll, and
their manifest; existing librime DLLs, plugins, and user data stay separate.
This script builds files only. It does not stop processes or install an IME.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$WeaselRoot,
    [Parameter(Mandatory)][string]$BoostRoot,
    [Parameter(Mandatory)][string]$RimeSdkX64,
    [Parameter(Mandatory)][string]$RimeSdkX86,
    [Parameter(Mandatory)][string]$PackageDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-Directory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Directory not found: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Resolve-RimeSdk([string]$Path, [string]$ExpectedLibraryHash) {
    $sdk = Resolve-Directory $Path
    if (Test-Path -LiteralPath (Join-Path $sdk 'dist\include\rime_api.h') -PathType Leaf) {
        $sdk = Join-Path $sdk 'dist'
    }
    $library = Join-Path $sdk 'lib\rime.lib'
    $header = Join-Path $sdk 'include\rime_api.h'
    # These are the official 1.17.0 rime-33e7814 Windows MSVC SDK inputs.
    # Pin the hashes because rime_api.h contains no release version macro.
    if ((Get-FileHash -LiteralPath $library -Algorithm SHA256).Hash -ne $ExpectedLibraryHash) {
        throw "Expected the official librime 1.17.0 import library: $library"
    }
    $headerHash = '85CAF744B4E5405A9A1DE9C7AEF3AFFC4AE315F4AE5D7EBDD08E191A2C16DAD4'
    if ((Get-FileHash -LiteralPath $header -Algorithm SHA256).Hash -ne $headerHash) {
        throw "Expected the official librime 1.17.0 API header: $header"
    }
    return $sdk
}

function Get-PeMachine([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5a4d) { throw "Not a PE binary: $Path" }
        $stream.Position = 0x3c
        $peOffset = $reader.ReadInt32()
        if ($peOffset -lt 0 -or $peOffset -gt ($stream.Length - 6)) {
            throw "Invalid PE header: $Path"
        }
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x4550) { throw "Invalid PE signature: $Path" }
        return $reader.ReadUInt16()
    } finally {
        $reader.Dispose()
    }
}

$WeaselRoot = Resolve-Directory $WeaselRoot
$BoostRoot = Resolve-Directory $BoostRoot
$rimeX64Hash = '28FE163E60BF930BA5C5CC688EA43429C594D2C7807F2C048AD5F717C8019D94'
$rimeX86Hash = '3D5532CE953A1B1AA9CD1DE7B86EFBB6019A1DFA8351854D5541E2B8AA2C6C13'
$RimeSdkX64 = Resolve-RimeSdk $RimeSdkX64 $rimeX64Hash
$RimeSdkX86 = Resolve-RimeSdk $RimeSdkX86 $rimeX86Hash
$PackageDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PackageDir)
if (Test-Path -LiteralPath $PackageDir) {
    if (-not (Test-Path -LiteralPath $PackageDir -PathType Container) -or
        @(Get-ChildItem -LiteralPath $PackageDir -Force).Count -ne 0) {
        throw "PackageDir must be a new or empty directory: $PackageDir"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $WeaselRoot 'include\SmartIMContext.h') -PathType Leaf)) {
    throw 'Apply the Smart IM native patch to the Weasel checkout first.'
}
$boostVersion = Get-Content -LiteralPath (Join-Path $BoostRoot 'boost\version.hpp') -Raw
if ($boostVersion -notmatch '(?m)^#define BOOST_VERSION 108400\s*$') {
    throw 'This build requires Boost 1.84.0.'
}
if (-not (Test-Path -LiteralPath (Join-Path $BoostRoot 'tools\build\src\engine\build.bat') -PathType Leaf)) {
    throw 'BoostRoot must contain the full Boost source archive, not only headers.'
}
$nativeCommit = & git -C $WeaselRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the native checkout commit.' }
& git -C $WeaselRoot merge-base --is-ancestor 0.17.4 HEAD
if ($LASTEXITCODE -ne 0) { throw 'The native checkout must descend from Weasel 0.17.4.' }
$nativeChanges = & git -C $WeaselRoot status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { throw 'Cannot read native checkout status.' }

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw 'Install Visual Studio 2022 C++ tools, ATL, and a Windows SDK first.'
}
$visualStudio = & $vswhere -latest -products '*' -version '[17.0,18.0)' `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $visualStudio) { throw 'Visual Studio 2022 C++ tools not found.' }
$vcvars = Join-Path $visualStudio 'VC\Auxiliary\Build\vcvarsall.bat'
$msbuild = Join-Path $visualStudio 'MSBuild\Current\Bin\MSBuild.exe'
foreach ($required in @($vcvars, $msbuild)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "File not found: $required" }
}
# Import into this process only, without printing an environment that may hold secrets.
$compilerEnvironment = & $env:ComSpec /d /s /c "`"$vcvars`" x64 >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize the MSVC build environment.' }
foreach ($entry in $compilerEnvironment) {
    $separator = $entry.IndexOf('=')
    if ($separator -gt 0) {
        [Environment]::SetEnvironmentVariable(
            $entry.Substring(0, $separator), $entry.Substring($separator + 1), 'Process'
        )
    }
}

$logDir = Join-Path $WeaselRoot 'msbuild\smart-im-build'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
Push-Location $BoostRoot
try {
    $b2 = Join-Path $BoostRoot 'b2.exe'
    if (-not (Test-Path -LiteralPath $b2 -PathType Leaf)) {
        & $env:ComSpec /d /s /c 'bootstrap.bat vc143'
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $b2 -PathType Leaf)) {
            throw 'Boost bootstrap failed.'
        }
    }
    # Boost 1.84 misdetects newer VS2022 minor toolsets; bind the setup path explicitly.
    $setupPath = $vcvars.Replace('\', '/')
    $jam = 'using msvc : 14.3 : : <setup>"' + $setupPath + '" ;' + [Environment]::NewLine
    [IO.File]::WriteAllText((Join-Path $BoostRoot 'project-config.jam'), $jam, $utf8)
    $boostArgs = @(
        '-j8', '--with-filesystem', '--with-json', '--with-locale', '--with-regex',
        '--with-serialization', '--with-system', '--with-thread',
        'define=BOOST_USE_WINAPI_VERSION=0x0603', 'toolset=msvc-14.3',
        'variant=release', 'link=static', 'runtime-link=static', 'threading=multi',
        'architecture=x86', '--layout=versioned', 'stage'
    )
    foreach ($bits in @(64, 32)) {
        Write-Output "Building Boost 1.84.0, address-model=$bits..."
        & $b2 @boostArgs "address-model=$bits" |
            Tee-Object -FilePath (Join-Path $logDir "boost-$bits.log")
        if ($LASTEXITCODE -ne 0) { throw "Boost $bits-bit build failed. See $logDir" }
    }
} finally {
    Pop-Location
}

$properties = [ordered]@{
    BOOST_ROOT = $BoostRoot
    PLATFORM_TOOLSET = 'v143'
    VERSION_MAJOR = '0'
    VERSION_MINOR = '17'
    VERSION_PATCH = '4'
    PRODUCT_VERSION = '0.17.4.0'
    FILE_VERSION = '0.17.4.0'
}
$props = Get-Content -LiteralPath (Join-Path $WeaselRoot 'weasel.props.template') -Raw
foreach ($name in $properties.Keys) {
    $props = $props.Replace('$' + $name, [Security.SecurityElement]::Escape($properties[$name]))
}
[xml]$validatedProps = $props
[IO.File]::WriteAllText((Join-Path $WeaselRoot 'weasel.props'), $props, $utf8)
$rimeInclude = Join-Path $WeaselRoot 'librime\include'
New-Item -ItemType Directory -Path $rimeInclude -Force | Out-Null
foreach ($header in Get-ChildItem -LiteralPath (Join-Path $RimeSdkX64 'include') -File) {
    Copy-Item -LiteralPath $header.FullName -Destination $rimeInclude -Force
}
foreach ($pair in @(@($RimeSdkX64, 'lib64'), @($RimeSdkX86, 'lib'))) {
    $destination = Join-Path $WeaselRoot $pair[1]
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $pair[0] 'lib\rime.lib') -Destination $destination -Force
}

Push-Location $WeaselRoot
try {
    $commonArgs = @('weasel.sln', '/m:8', '/nologo', '/p:Configuration=Release', '/p:PlatformToolset=v143')
    Write-Output 'Building x64 WeaselTSF and WeaselServer...'
    & $msbuild @commonArgs '/t:WeaselTSF;WeaselServer' '/p:Platform=x64' `
        "/flp:logfile=$(Join-Path $logDir 'weasel-x64.log');verbosity=normal"
    if ($LASTEXITCODE -ne 0) { throw "Weasel x64 build failed. See $logDir" }
    # The Win32 server would overwrite output\WeaselServer.exe; build only the TIP.
    Write-Output 'Building Win32 WeaselTSF...'
    & $msbuild @commonArgs '/t:WeaselTSF' '/p:Platform=Win32' `
        "/flp:logfile=$(Join-Path $logDir 'weasel-x86.log');verbosity=normal"
    if ($LASTEXITCODE -ne 0) { throw "Weasel Win32 build failed. See $logDir" }
} finally {
    Pop-Location
}

$machines = [ordered]@{ 'WeaselServer.exe' = 0x8664; 'weaselx64.dll' = 0x8664; 'weasel.dll' = 0x014c }
foreach ($name in $machines.Keys) {
    $source = Join-Path $WeaselRoot "output\$name"
    if ((Get-PeMachine $source) -ne $machines[$name]) { throw "Incorrect binary architecture: $source" }
}
New-Item -ItemType Directory -Path $PackageDir -Force | Out-Null
$hashes = [ordered]@{}
foreach ($name in $machines.Keys) {
    Copy-Item -LiteralPath (Join-Path $WeaselRoot "output\$name") -Destination $PackageDir
    $hashes[$name] = (Get-FileHash -LiteralPath (Join-Path $PackageDir $name) -Algorithm SHA256).Hash
}
$manifest = [ordered]@{
    base = '0.17.4'
    native_commit = $nativeCommit.Trim()
    native_dirty = [bool]$nativeChanges
    boost = '1.84.0'
    rime = '1.17.0'
    rime_sdk_source = 'https://github.com/rime/librime/releases/tag/1.17.0'
    rime_import_lib_sha256 = [ordered]@{ x64 = $rimeX64Hash; x86 = $rimeX86Hash }
    platform_toolset = 'v143'
    msvc_tools_version = $env:VCToolsVersion
    files = $hashes
}
[IO.File]::WriteAllText((Join-Path $PackageDir 'manifest.json'), ($manifest | ConvertTo-Json -Depth 5), $utf8)
Write-Output "Native package: $PackageDir"
Write-Output "Build logs: $logDir"
