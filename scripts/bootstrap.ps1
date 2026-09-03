param(
    [string]$PythonPath = "python",
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

Push-Location $projectRoot
try {
    & $PythonPath -m pip install -r requirements-web.txt
    if ($LASTEXITCODE -ne 0) { throw "Web dependency installation failed." }
    & $PythonPath -m pip install --no-deps -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Runtime snapshot installation failed." }
    & $PythonPath -m pip install --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw "Project installation failed." }
    if ($Dev) {
        & $PythonPath -m pip install -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw "Development dependency installation failed." }
    }
}
finally {
    Pop-Location
}
