param(
    [string]$WeaselRoot = "",
    [string]$BoostRoot = "",
    [string]$VcVarsPath = "",
    [switch]$CompileSources
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $WeaselRoot) {
    $WeaselRoot = Join-Path $projectRoot "artifacts\weasel-smart-im"
}
$WeaselRoot = (Resolve-Path -LiteralPath $WeaselRoot).Path
if ($BoostRoot) { $BoostRoot = (Resolve-Path -LiteralPath $BoostRoot).Path }
if ($CompileSources -and -not $BoostRoot) {
    throw "-CompileSources requires -BoostRoot with Boost 1.84 headers."
}
$testSource = Join-Path $WeaselRoot "test\SmartIMContextTest.cpp"
if (-not (Test-Path -LiteralPath $testSource -PathType Leaf)) {
    throw "Apply the Smart IM Weasel patch before running native tests: $testSource"
}

if (-not $VcVarsPath) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
        throw "Visual Studio C++ tools not found. Supply -VcVarsPath with vcvars64.bat."
    }
    $visualStudio = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $visualStudio) {
        throw "Visual Studio C++ tools not found. Supply -VcVarsPath with vcvars64.bat."
    }
    $VcVarsPath = Join-Path $visualStudio "VC\Auxiliary\Build\vcvars64.bat"
}
$VcVarsPath = (Resolve-Path -LiteralPath $VcVarsPath).Path
# Import only into this process; do not print the environment (it may hold secrets).
$compilerEnvironment = & $env:ComSpec /d /s /c "`"$VcVarsPath`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw "Failed to initialize the MSVC build environment." }
foreach ($entry in $compilerEnvironment) {
    $separator = $entry.IndexOf('=')
    if ($separator -gt 0) {
        [Environment]::SetEnvironmentVariable(
            $entry.Substring(0, $separator), $entry.Substring($separator + 1), "Process"
        )
    }
}

$outputRoot = Join-Path $projectRoot "artifacts\native-tests"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$testBinary = Join-Path $outputRoot "SmartIMContextTest.exe"
$testObject = Join-Path $outputRoot "SmartIMContextTest.obj"
$compilerArgs = @(
    "/nologo", "/std:c++17", "/EHsc", "/W4", "/WX", "/utf-8",
    "/I$(Join-Path $WeaselRoot 'include')", "/Fo$testObject", "/Fe$testBinary", $testSource
)
Push-Location $outputRoot
try {
    & cl.exe @compilerArgs
    if ($LASTEXITCODE -ne 0) { throw "Smart IM IPC parser native compilation failed." }
    & $testBinary
    if ($LASTEXITCODE -ne 0) { throw "Smart IM IPC parser native tests failed." }
    $localContextBinary = Join-Path $outputRoot "LocalContextTest.exe"
    $localContextArgs = @(
        "/nologo", "/std:c++17", "/EHsc", "/W3", "/utf-8", "/DUNICODE", "/D_UNICODE",
        "/Fo$(Join-Path $outputRoot 'LocalContextTest.obj')", "/Fe$localContextBinary",
        (Join-Path $WeaselRoot "WeaselTSF\tests\LocalContextTest.cpp"),
        "/link", "ole32.lib", "oleaut32.lib", "uuid.lib"
    )
    & cl.exe @localContextArgs
    if ($LASTEXITCODE -ne 0) { throw "Smart IM TSF range native compilation failed." }
    & $localContextBinary
    if ($LASTEXITCODE -ne 0) { throw "Smart IM TSF range native tests failed." }
    if ($BoostRoot) {
        $pipeSource = Join-Path $WeaselRoot "test\SmartIMPipeTest.cpp"
        $pipeImplementation = Join-Path $WeaselRoot "WeaselIPC\PipeChannel.cpp"
        $pipeBinary = Join-Path $outputRoot "SmartIMPipeTest.exe"
        $pipeArgs = @(
            "/nologo", "/std:c++17", "/EHsc", "/W4", "/utf-8", "/DBOOST_ALL_NO_LIB",
            # This executable needs no Boost DLL exports; avoid forcing unused
            # archive facets from Weasel's precompiled-header includes to link.
            "/DUNICODE", "/D_UNICODE", "/DBOOST_SYMBOL_EXPORT=",
            "/DBOOST_SYMBOL_IMPORT=__declspec(dllimport)",
            "/I$(Join-Path $WeaselRoot 'include')", "/I$BoostRoot", "/Fe$pipeBinary",
            $pipeSource, $pipeImplementation
        )
        & cl.exe @pipeArgs
        if ($LASTEXITCODE -ne 0) { throw "Smart IM named-pipe native compilation failed." }
        & $pipeBinary
        if ($LASTEXITCODE -ne 0) { throw "Smart IM named-pipe native tests failed." }
    }
    if ($CompileSources) {
        $objectRoot = Join-Path $projectRoot "artifacts\native-objects"
        New-Item -ItemType Directory -Path $objectRoot -Force | Out-Null
        $sources = @(
            "WeaselTSF\LocalContext.cpp", "WeaselTSF\KeyEventSink.cpp",
            "WeaselTSF\TextEditSink.cpp", "WeaselTSF\ThreadMgrEventSink.cpp",
            "WeaselTSF\WeaselTSF.cpp", "WeaselTSF\Composition.cpp", "WeaselTSF\DisplayAttribute.cpp",
            "WeaselTSF\EditSession.cpp",
            "WeaselIPC\WeaselClientImpl.cpp", "WeaselIPC\PipeChannel.cpp",
            "WeaselIPCServer\WeaselServerImpl.cpp", "RimeWithWeasel\RimeWithWeasel.cpp"
        )
        foreach ($source in $sources) {
            $objectName = $source.Replace('\', '_').Replace('.cpp', '.obj')
            $sourceArgs = @(
                "/nologo", "/c", "/std:c++17", "/EHsc", "/W3", "/utf-8",
                "/DUNICODE", "/D_UNICODE", "/DBOOST_ALL_NO_LIB",
                "/DVERSION_MAJOR=0", "/DVERSION_MINOR=17", "/DVERSION_PATCH=4",
                "/I$(Join-Path $WeaselRoot 'include')", "/I$BoostRoot",
                "/I$(Join-Path $WeaselRoot 'librime\src')",
                "/Fo$(Join-Path $objectRoot $objectName)", (Join-Path $WeaselRoot $source)
            )
            & cl.exe @sourceArgs
            if ($LASTEXITCODE -ne 0) { throw "Native translation unit compilation failed: $source" }
        }
        Write-Output "Compiled $($sources.Count) Weasel translation units (object files only)."
    }
} finally {
    Pop-Location
}
