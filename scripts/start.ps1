param(
    [ValidateSet("desktop", "windows")]
    [string]$Mode = "desktop",
    [switch]$Learn
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "请先安装 Python 3.10 或更新版本。" }
    & $projectPython -m pip install -e ".[desktop]"
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败。请检查终端输出。" }
}
$launchArgs = @("-m", "smart_im")
if ($Learn) { $launchArgs += "--learn" }
$launchArgs += $Mode
& $projectPython @launchArgs
exit $LASTEXITCODE
