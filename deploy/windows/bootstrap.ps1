param(
    [string]$PythonVersion = "3.11.15",
    [ValidateSet("paddle-gpu-cu129", "paddle-cpu")]
    [string]$PaddleRuntime = "paddle-gpu-cu129"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$serviceRoot = Join-Path $projectRoot "services\document-extraction"
$venvPython = Join-Path $serviceRoot ".venv\Scripts\python.exe"

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvExe = $uvCommand.Source
} else {
    $uvPath = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (-not (Test-Path -LiteralPath $uvPath)) {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    }
    $uvExe = (Get-Item -LiteralPath $uvPath).FullName
}

& $uvExe python install $PythonVersion
& $uvExe sync --project $serviceRoot --extra paddle --extra gpu-cu129 --extra $PaddleRuntime --python $PythonVersion
& $uvExe pip check --python $venvPython
& (Join-Path $serviceRoot ".venv\Scripts\history-extract.exe") check

Write-Host "Ready: $venvPython"
