# Hesabatım

> **Status: INACTIVE / PAUSED (since 2026-10-27).** See [PROJECT_MEMORY.md](PROJECT_MEMORY.md) for the full context needed to resume.

AI-first inventory and sales workspace in Azerbaijani. Next.js + TypeScript/Tailwind, FastAPI + SQLAlchemy/Pydantic, PostgreSQL. SQLite is a clearly labeled local development option. The Second Brain knowledge base was only read; it was not modified.

## Start locally (Windows / PowerShell)

Dependencies are already present in this workspace. For a fresh checkout, install Python 3.11+ and Node 22+, then:

```powershell
cd E:\hesabatim\backend
uv sync --group dev
Copy-Item .env.example .env
cd ..\frontend
npm.cmd ci
Copy-Item .env.local.example .env.local
```

Run in two terminals:

```powershell
# Terminal 1: migrations and API
cd E:\hesabatim
.\start-backend.ps1
```

```powershell
# Terminal 2: website
cd E:\hesabatim
.\start-frontend.ps1
```

Open **http://localhost:3000**. API documentation: **http://127.0.0.1:8000/docs**. Register your own account; no demo credentials or fake business transactions are installed in the application database. Browser smoke tests use a separate database.

Without uv, create a Python virtual environment and install the dependency list from `backend/pyproject.toml` using pip. The existing `.venv` can run the supplied start script directly.

## Try the complete flow

1. Register with your name, business name, email and password.
2. Write `Məndə 500 qapı, 300 taxta və 150 masa var.`
3. Inspect the three proposed products. Nothing has been saved yet. Click **Təsdiqlə** on individual cards or write **Bəli** to confirm the latest displayed batch. Each action is its own atomic transaction; a failure on one card does not undo other confirmed cards.
4. Write `Bu gün 20 qapını 150 manatdan satdım.` Confirm the sale.
5. Inventory shows 480 doors; the dashboard shows 3,000 AZN revenue. Purchase cost is unknown, so total gross profit is not reported as known.
6. Ask `Bu gün nə qədər satış etmişəm?`, `Bu ay nə qədər gəlir əldə etmişəm?`, `Keçən ayla müqayisədə satışlarım necədir?` or `Hansı məhsullardan az qalıb?`.
7. Record manual sales, partial returns/full cancellations in **Satışlar**. Original history remains available.

## Environment

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Local default `sqlite:///./hesabatim_dev.db`; production `postgresql+psycopg://USER:PASSWORD@HOST:5432/DB` |
| `ENVIRONMENT` | `development`, `test` or `production`; production requires PostgreSQL and secure cookies |
| `JWT_SECRET` | Random session signing secret, at least 32 characters; mandatory strong value in production |
| `JWT_TTL_MINUTES` | Session expiry, default 10,080 minutes; logout-all/password change revoke existing tokens |
| `CORS_ORIGINS` | Exact browser origins, comma separated; default `http://localhost:3000`; also used for cookie CSRF Origin checks |
| `LLM_API_KEY` | Leave empty for the labeled deterministic development parser |
| `LLM_BASE_URL` | Backend-only OpenAI-compatible chat-completions base URL |
| `LLM_MODEL` | Tool-calling model supported by your configured provider |
| `LLM_TIMEOUT_SECONDS` | Provider timeout (60 seconds by default) |
| `API_BACKEND_URL` | Next.js server environment; defaults to `http://127.0.0.1:8000`; configure before build/start |

| `ADMIN_PATH` | Secret URL segment (backend **and** frontend): panel at `/<ADMIN_PATH>`, API at `/api/v1/<ADMIN_PATH>`. `/admin` does not exist |
| `ADMIN_ALLOWED_IPS` | Backend: IPs/CIDRs allowed to the admin API. Frontend: exact IPs allowed to the panel page. Everyone else gets 404 |
| `FRONTEND_PROXY_SECRET` | Shared by frontend and backend; lets `src/proxy.ts` pass the real client IP (`X-Client-IP`) to FastAPI |
| `TRUST_X_REAL_IP` | Frontend: `true` only behind Railway's edge, which sets `X-Real-IP` |
| `SADAKAT_WEBHOOK_URL` / `SADAKAT_WEBHOOK_SECRET` | Optional signed card-change webhooks to SadakatKarti |

Additional upload/rate-limit settings are in `backend/.env.example`.

## Admin panel

Read-only. Reachable only at the secret `/<ADMIN_PATH>` from an IP in `ADMIN_ALLOWED_IPS`; every other address, and every guessable path (`/admin`, `/adminpanel`, `/api/v1/admin`), gets an ordinary 404. The Next.js proxy (`frontend/src/proxy.ts`) strips client-supplied `X-Client-IP`/`X-Frontend-Secret` headers and sets them itself; the backend trusts that IP only with the shared secret, re-checks the allowlist, binds the session cookie to the IP, and locks the IP out for `ADMIN_LOCKOUT_MINUTES` after `ADMIN_MAX_FAILURES` wrong passwords (restart the backend to clear a lockout). Every admin request, block and probe is logged to stdout (Railway logs) and to `audit_logs`, visible in the panel.

## Railway

Services: `Postgres`, `backend` (root `backend/`, Dockerfile, `railway.json` runs `alembic upgrade head` pre-deploy, private networking only, `PORT=8000`) and `frontend` (root `frontend/`, public domain, `API_BACKEND_URL=http://backend.railway.internal:8000`, `TRUST_X_REAL_IP=true`). Backend `DATABASE_URL=${{Postgres.DATABASE_URL}}` (the `postgresql://` scheme is converted to psycopg automatically) and `CORS_ORIGINS=https://<frontend domain>`. Do not put secrets in `NEXT_PUBLIC_*`. Provider changes require a backend restart. Live provider quality, availability and billing have not been tested without credentials. The offline parser handles the documented examples and a tested rule set; it is not a general language model.

## PostgreSQL

Create a dedicated database and non-superuser application role, configure `DATABASE_URL`, then:

```powershell
cd backend
uv run alembic upgrade head
uv run alembic check
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Migrations are versioned under `backend/alembic/versions`. Run migrations as a release step, not concurrently on every worker. See [operations and backup/restore procedures](docs/OPERATIONS.md).

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest

# Uses and CLEARS a disposable database; its name must end in _test.
$env:TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:5432/hesabatim_test'
.\.venv\Scripts\python.exe -m pytest
Remove-Item Env:TEST_DATABASE_URL

cd ..\frontend
npm.cmd run build
```

The dependency-free browser smoke test uses Node's WebSocket client and an installed Chromium through CDP:

```powershell
# First run a separate backend on 8010 with DATABASE_URL=sqlite:///./browser_test.db
# and CORS_ORIGINS=http://localhost:3010; run Next.js on 3010 with API_BACKEND_URL=http://127.0.0.1:8010.
cd frontend
$env:CHROME_PATH='C:\path\to\chrome.exe'
node scripts/browser-smoke.mjs
```

It creates an isolated test account, checks real browser registration, AI previews/confirmation, sales/stock/revenue, dashboard, manual product creation, cancellation, settings, dark/mobile layout and HttpOnly session privacy. Screenshots are saved under `docs/screenshots`. Its Chromium `--no-sandbox` flag is for this restricted local test environment only; do not browse untrusted sites with that test instance.

## Implemented boundaries and remaining limits

* Both manual and AI operations call the same services. The LLM cannot execute SQL or confirm writes. Model tool arguments are schema-validated; business IDs come from authenticated membership.
* Browser tokens are HttpOnly cookies, not localStorage. API clients can use bearer tokens. Send a stable **Idempotency-Key** on retries of product, stock, sale and return mutations. Reusing a key with different arguments is rejected. AI confirmation replays are inherently idempotent.
* PostgreSQL row locks and SQLite serialized writes protect stock; failed multiline sales roll back all changes. Refunds are separate ledger entries. Money crosses the API as decimal strings. Unit costs are snapshotted at sale time.
* CSV/XLSX import supports preview and per-row error reporting, then applies valid rows. Export includes all active products. Category management, low-stock alerts, product archive/restore, search and pagination are implemented.
* Provider selection is deployment configuration, visible in Settings; users cannot enter secrets in the browser. OpenAI-compatible providers are supported by a small adapter, not arbitrary vendor-specific APIs.
* No subscription billing, email verification/reset, MFA, invitations, supplier invoices/purchase orders, FIFO costing, expenses/tax accounting or production hosting is included. Receiving purchases is recorded as stock adjustment with a reason; it is not a purchasing ledger.
* Single-process rate limits, application-memory report aggregation and operator-managed backups/retention should be upgraded for larger production scale. Analytics date filters use the business timezone; the UI transaction date display currently uses Asia/Baku.

Architecture sources and decisions: [ARCHITECTURE.md](docs/ARCHITECTURE.md). Verification evidence: [TEST_RESULTS.md](docs/TEST_RESULTS.md). Earlier unrelated marketplace code/tests are preserved locally in `archive/`, outside the active build and test paths.
