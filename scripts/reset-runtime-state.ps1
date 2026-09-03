param(
    [string]$PythonPath = "python",
    [switch]$ConfirmReset
)

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
    $arguments = @("-m", "travel_agent_orchestrator.infrastructure.reset_state")
    if ($ConfirmReset) { $arguments += "--confirm-reset" }
    & $PythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw "Runtime state reset failed." }
}
finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
