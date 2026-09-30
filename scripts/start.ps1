param(
    [ValidateSet("serve", "install-rime")]
    [string]$Mode = "serve",
    [switch]$Learn,
    [ValidateSet("tiny", "ollama")]
    [string]$Backend = "tiny",
    [string]$Model = "qwen3:1.7b",
    [ValidateRange(0.1, 20)]
    [double]$ModelTimeout = 10
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $projectPython)) {
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "请先安装 Python 3.10 或更新版本。" }
}
& $projectPython -c "import smart_im, numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $projectPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败。请检查终端输出。" }
}
$launchArgs = @("-m", "smart_im")
if ($Learn) { $launchArgs += "--learn" }
$launchArgs += $Mode
if ($Mode -eq "serve") {
    $launchArgs += @("--backend", $Backend, "--model", $Model, "--model-timeout", $ModelTimeout.ToString([Globalization.CultureInfo]::InvariantCulture))
}
& $projectPython @launchArgs
exit $LASTEXITCODE
