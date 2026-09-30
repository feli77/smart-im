param(
    [ValidateSet("serve", "install-rime", "desktop", "windows")]
    [string]$Mode = "serve",
    [switch]$Learn
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "请先安装 Python 3.10 或更新版本。" }
}
$packageSpec = "."
$importCheck = "import smart_im"
if ($Mode -in @("desktop", "windows")) {
    $packageSpec = ".[desktop]"
    $importCheck = "import smart_im, PySide6"
}
& $projectPython -c $importCheck 2>$null
if ($LASTEXITCODE -ne 0) {
    & $projectPython -m pip install -e $packageSpec
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败。请检查终端输出。" }
}
$launchArgs = @("-m", "smart_im")
if ($Learn) { $launchArgs += "--learn" }
$launchArgs += $Mode
& $projectPython @launchArgs
exit $LASTEXITCODE
