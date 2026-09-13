$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$modelRoot = Join-Path $projectRoot "models"
$manifest = Join-Path $modelRoot "manifest.sha256"

$lines = Get-ChildItem -LiteralPath $modelRoot -Recurse -File |
    Where-Object { $_.FullName -ne $manifest -and $_.Name -ne ".gitkeep" } |
    Sort-Object FullName |
    ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($modelRoot, $_.FullName).Replace("\", "/")
        "{0}  {1}" -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
    }

Set-Content -LiteralPath $manifest -Value $lines -Encoding utf8
Write-Host "Wrote $($lines.Count) hashes to $manifest"
