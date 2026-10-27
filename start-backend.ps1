$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Join-Path $PSScriptRoot 'backend')
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    throw 'Install backend dependencies first: cd backend; uv sync --group dev'
}
& '.\.venv\Scripts\python.exe' -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
& '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
