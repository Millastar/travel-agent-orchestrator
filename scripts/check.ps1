param([string]$PythonPath = "python")

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location $projectRoot
try {
    & $PythonPath -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { throw "Ruff check failed." }
    & $PythonPath -m ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { throw "Ruff format check failed." }
    & $PythonPath -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Pytest failed." }
    & $PythonPath -m travel_agent_orchestrator.evaluation
    if ($LASTEXITCODE -ne 0) { throw "Offline evaluation failed." }

    $sourceFiles = Get-ChildItem -LiteralPath src, tests, docs, scripts -Recurse -File
    $secretMatches = $sourceFiles | Select-String -Pattern 'sk-[A-Za-z0-9_-]{20,}'
    if ($secretMatches) {
        throw "Potential hard-coded API key detected."
    }
}
finally {
    Pop-Location
}
