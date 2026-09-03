param([string]$PythonPath = "python")

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot "src")).Path
$previousPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourceRoot$([IO.Path]::PathSeparator)$previousPythonPath"
    }
    else {
        $sourceRoot
    }
    & $PythonPath -m travel_agent_orchestrator.infrastructure.database
    if ($LASTEXITCODE -ne 0) { throw "Database initialization failed." }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
