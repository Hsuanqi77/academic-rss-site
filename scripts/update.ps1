$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Virtual environment missing. Run: py -3.11 -m venv .venv'
}

Push-Location $Root
try {
    & $Python -m paper_radar update
    if ($LASTEXITCODE -ne 0) {
        throw "Update failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
