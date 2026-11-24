$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    $python = '.\.venv\Scripts\python.exe'
    if (-not (Test-Path $python -PathType Leaf)) {
        throw 'Create the Python virtual environment and install the project first. See README.md.'
    }
    if (-not (Test-Path 'frontend/dist/index.html' -PathType Leaf)) {
        throw 'Run pnpm install and pnpm build in the frontend directory first.'
    }
    $port = if ($env:TRACELENS_PORT) { $env:TRACELENS_PORT } else { '8017' }
    # Use Python's default Windows event loop to support diagnostic subprocesses.
    & $python -m uvicorn tracelens.main:app --host 127.0.0.1 --port $port --loop none
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
