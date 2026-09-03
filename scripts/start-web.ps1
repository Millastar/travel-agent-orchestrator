param([string]$PythonPath = "python")

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$sourceRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot "src")).Path
$previousPythonPath = $env:PYTHONPATH
Push-Location $projectRoot
try {
    # Pin this process to the current checkout so another editable install
    # cannot be selected from site-packages.
    $env:PYTHONPATH = if ($previousPythonPath) {
        "$sourceRoot$([IO.Path]::PathSeparator)$previousPythonPath"
    }
    else {
        $sourceRoot
    }

    & $PythonPath -c "import pathlib, sys, travel_agent_orchestrator; package = pathlib.Path(travel_agent_orchestrator.__file__).resolve(); source = pathlib.Path(sys.argv[1]).resolve(); raise SystemExit(0 if package.is_relative_to(source) else 2)" $sourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to load travel_agent_orchestrator from the current project."
    }

    Write-Host "Starting Travel Agent Orchestrator at http://localhost:8002"
    & $PythonPath -m travel_agent_orchestrator.api
    if ($LASTEXITCODE -ne 0) { throw "Web service exited with an error." }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
