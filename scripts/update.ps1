$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Virtual environment missing. Run: py -3 -m venv .venv (Python 3.11 or newer required)'
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
