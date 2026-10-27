# Hesabatım: project memory

> **STATUS: INACTIVE / PAUSED (since 2026-10-27).** Do not continue work unless the user explicitly asks.
> The production deployment on Railway was left running. No secrets are stored in this file.

## What it is
A multi-tenant web platform for small businesses in Azerbaijan, with the UI and messages in Azerbaijani. It covers:
- Inventory, with an event journal.
- Sales, returns and cancellations.
- Customers and a loyalty stamp card.
- Notifications.
- Sales analytics.
- An AI chat assistant that proposes changes the user must confirm.
- A read-only admin panel.

It integrates with **SadakatKarti**, a separate customer-facing loyalty-card site with its own codebase, run by another session or agent.

Further docs: `README.md` (local run, env, tests), `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/TEST_RESULTS.md`.

Architecture artifact (Azerbaijani, v3, includes a risk list): https://claude.ai/artifact/6RYyveUzBGNjX5te5kQyxp

## Tech stack

**Backend** (`backend/`)
- Python with FastAPI, SQLAlchemy 2, Alembic, Pydantic settings; `uv` for dependencies.
- PostgreSQL in production (psycopg 3); SQLite for local dev, where tables are created directly.
- argon2id for password hashes, PyJWT with HS256.

**Frontend** (`frontend/`)
- Next.js 16.4.3 (pinned; 16.4.0 had RCE advisories), React, Recharts, `qrcode.react`.
- `src/proxy.ts` replaces middleware and runs on the Node runtime.

**Hosting:** Railway, project `hesabatim`, environment `production`.

## Repository layout
**Backend**
- `backend/app/main.py`: app factory, middleware (request id, security headers, JSON request log with IP), error handlers, router mounting, `/health`.
- `backend/app/core/`
  - `config.py`: settings and production validations.
  - `security.py`: JWT and argon2.
  - `rate_limit.py`: in-process sliding window.
  - `errors.py`
- `backend/app/api/deps.py`: `current_user`, `Member` (role check), `trusted_client_ip`, rate-limit keys.
- `backend/app/api/routes/`
  - `auth.py`, `business.py` (everything under `/businesses/{id}`), `ai.py` (SSE), `public.py` (card and signup).
- `backend/app/services/`
  - products, inventory, sales, customers, analytics, notifications, accounts, categories, lexicon, common.
  - `sadakat_webhook.py`
- `backend/app/ai/`
  - `orchestrator.py`, `tools.py`, `guards.py`, `nlu.py`, `rules.py`, `prompts.py`
  - `providers/`: OpenAI-compatible provider, plus an offline rule-based parser used when `LLM_API_KEY` is unset (the current state in production).
- `backend/alembic/versions/`: `e00e46877e65` initial → `513a81c1461a` customers, loyalty and branding → `7c1e2d9a4b10` `businesses.notifications` (head).
- `backend/tests/`: pytest; **120 passing** at pause time.

**Frontend**
- `frontend/src/components/`
  - workspace (nav, bell, 60 s polling)
  - inventory, sales, customers, notifications, settings, loyalty, chat, dashboard, excel-import, public-card
- `frontend/src/app/`: `/`, `/kart/[token]`.
- `frontend/src/lib/api.ts`: same-origin fetch to `/api/v1` with credentials.

**Other:** `archive/` holds old frontend sources and tests (unused). `start-backend.ps1` and `start-frontend.ps1` start local dev.

## Data model (16 tables)
**Accounts:** `profiles` (token_version), `local_credentials`, `business_members` (owner, admin, staff).

**`businesses`:** slug, currency, timezone, logo as a data URI (max 512 KB), and JSON columns:
- `theme`: `{brand, card}`
- `notifications`: `{settings, seen[]}`
- loyalty_*

**Catalog:**
- `categories` (folded `name_key`)
- `products` (sku, unit, prices, low_stock_threshold, search_text)
- `product_aliases`

**Stock:**
- `inventory`: one row per product; has quantity and version.
- `inventory_events`: before/after, source, `sale_id`, `ai_action_id`, `reverts_event_id`, `idempotency_key`.

**Sales:**
- `sales`: S-/R- numbers; kind sale/return; status; `cost_total` is NULL when any line has unknown cost.
- `sale_items`: name and unit snapshots.

**Loyalty:**
- `customers`: phone unique per business, global `card_token`, stamps, rewards, tags.
- `loyalty_events`

**AI:** `conversations`, `messages`, `ai_actions` (pending → executed / rejected / failed).

**Other:** `audit_logs`, `idempotency_keys`.

**Invariants** are enforced by DB CHECK and UNIQUE constraints: non-negative quantities and prices, and a return requires its original sale.

## Key technical decisions
**Stock**
- Changes are append-only events. Undo writes a reverse event and never deletes.
- Writes lock rows with `SELECT … FOR UPDATE`. An optional `expected_version` gives a 409 `stale_version` on mismatch.

**Sales**
- Numbering is serialized by locking the business row.
- `Idempotency-Key` is supported on sales and stock writes.
- Money uses `Decimal` and is sent as strings in the API; floats are used only for charts.

**AI**
- Write tools create a preview (an `ai_actions` row with status pending). Changes run only after the user confirms.
- Guards (block / fix / warn) run in both LLM and offline modes.
- `ungrounded_amounts` flags money figures in the assistant's text that are not grounded in tool results or the conversation.

**Notifications**
- Computed live on each `GET /notifications`; there are no rows.
- Keys: `out:{pid}`, `low:{pid}:{qty}`, `reward:{cid}:{rewards}`, `bday:{cid}:{year}`, `cust:{cid}` (customers tagged "QR qeydiyyat" in the last 7 days).
- "Seen" is the set of keys saved in `businesses.notifications.seen`.
- The low-stock threshold is either each product's own value or one global value.

**Errors:** always `{"error":{code,message,details}}`, with Azerbaijani messages.

## Security model
**User auth**
- JWT (7 days, carries `ver`) in an HttpOnly `hesabatim_session` cookie, SameSite=Lax.
- Non-GET requests made with the cookie require an `Origin` listed in `CORS_ORIGINS`.
- Logout-all and password change bump `token_version`, which invalidates all existing tokens.

**Client IP trust chain**
1. Railway edge sets `X-Real-IP`.
2. `proxy.ts` strips the browser's `x-frontend-secret` and `x-client-ip`, then sets its own (only when `TRUST_X_REAL_IP=true`).
3. The backend trusts `X-Client-IP` only together with `FRONTEND_PROXY_SECRET`, compared with HMAC.

SadakatKarti requests carry `X-Proxy-Secret`, so the rate limit counts the end user's `X-Forwarded-For` address.

**Rate limits** (in-process): 120/min general; 10/min for auth and public signup; AI 20/min and 300/day.

**Admin panel:** local-only, not in git (`backend/app/admin_private/`, gitignored). Its design, deploy steps and secrets locations are in `.local/ADMIN.md` on the developer machine.

**Startup checks:** in production, the secrets must be 32+ characters and SQLite is refused.

**No `NEXT_PUBLIC_*` variables:** no key or the admin path reaches the browser.

## SadakatKarti integration
**Signup (SadakatKarti → Hesabatım)**
- The business shows the QR of `signup_url` = `${SADAKAT_PUBLIC_URL}/k/<slug>`. The "Qeydiyyat QR-ı" button is in Müştərilər.
- The customer fills in a form on SadakatKarti, which calls `POST /api/v1/public/businesses/{slug}/cards`.
- This creates a customer tagged "QR qeydiyyat", or returns the existing card for the same phone.
- The business scans the card; `GET /customers/by-card/{token}` returns the name and phone.

**Webhook (Hesabatım → SadakatKarti)**
- URL: `SADAKAT_WEBHOOK_URL` (internal, `sadakatkarti.railway.internal:8080/api/integrations/hesabatim/webhook`).
- Signature: HMAC-SHA256 over `"{ts}.{body}"`. Headers `X-Sadakat-Timestamp` and `X-Sadakat-Signature: sha256=…`.
- Events: `loyalty.stamp|redeem|adjust|new_card`, `customer.created`, `business.created|updated`.
- Business events carry name, stamps_needed, reward, loyalty_enabled, logo and theme (card colour included). Phone numbers are never sent.
- Delivery: after_flush collects events, after_commit sends them from a 2-worker thread pool with a 3 s timeout, and rollback drops them. At-most-once, no retry.

## Deployment (Railway)
**Services**
- `frontend` (public): https://hesabatim.up.railway.app (old: https://frontend-production-2d55e.up.railway.app). Every frontend domain must be listed in the backend `CORS_ORIGINS`, otherwise all saves (non-GET requests) get 403 "Sorğunun mənbəyi təsdiqlənmədi".
- `backend`: private, `backend.railway.internal:8000`, 1 replica.
- `Postgres`
- `sadakatkarti` (public, port 8080): https://sadakatkarti-production.up.railway.app
- `Postgres-i46k`: SadakatKarti's database.

**Source:** GitHub `ulvi986/hesabatim` (public), branch `main`.
- `frontend` auto-deploys on push (root `/frontend`, watch `/frontend/**`).
- `backend` is **not** connected to GitHub since 2026-10-28 (it needs local-only modules). Deploy it from `backend/` with `railway up . --path-as-root --no-gitignore --service backend --environment production --ci` (root directory `/`; `.railwayignore` keeps `.env`, `.venv` and tests out).
- The backend has **no healthcheck** on purpose: uvicorn binds IPv6-only (`--host ::`) and Railway's healthcheck comes over IPv4, so `/health` never passes. Don't add `healthcheckPath` back without changing the bind.
- Railway still reads `railway.json` from the service root, but rejects setting `railwayConfigFile` via API (config-as-code is deprecated); service settings were set via the GraphQL API.

**Backend container**
- The Dockerfile CMD runs `alembic upgrade head`, then `uvicorn --host :: --no-proxy-headers` (IPv6 is needed for private networking).
- Railway ignored `preDeployCommand`, which is why migrations run in the CMD instead.
- Healthcheck: `/health`.

**Variables**
- Backend:
  - `DATABASE_URL=${{Postgres.DATABASE_URL}}`
  - `CORS_ORIGINS`, `JWT_SECRET`
  - `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `ADMIN_JWT_SECRET`, `ADMIN_PATH`, `ADMIN_ALLOWED_IPS`
  - `FRONTEND_PROXY_SECRET`, `PROXY_SECRET`
  - `SADAKAT_WEBHOOK_URL`, `SADAKAT_WEBHOOK_SECRET=${{sadakatkarti.HESABATIM_WEBHOOK_SECRET}}`, `SADAKAT_PUBLIC_URL`
  - Not set: `LLM_API_KEY`, `SENTRY_DSN`.
- Frontend:
  - `API_BACKEND_URL`, `TRUST_X_REAL_IP=true`, `ADMIN_ALLOWED_IPS`
  - `ADMIN_PATH` and `FRONTEND_PROXY_SECRET` as references to the backend's values.

**Secrets live in:** `backend/.env`, `frontend/.env.local`, the Railway variables, and the session scratchpad `railway-secrets.json`.
- `.railwayignore` excludes the env files, `.venv`, `node_modules`, `.next` and `*.db`.

**Tooling notes**
- Use the `railway` CLI. The Railway MCP server had a stale token.
- `railway login` must run in a separate interactive PowerShell window.
- The admin IP may change because it is a home ISP. If the admin panel returns 404, update `ADMIN_ALLOWED_IPS`.

## Features delivered (latest work, 2026-10-26/27)
- **Admin panel:** rebuilt, with the secret path, IP-only access and full logging.
- **Deployment:** Railway.
- **QR signup:** for SadakatKarti; visible only in Müştərilər.
- **Bildirişlər:**
  - Bell with a badge.
  - Settings: per-product or global threshold, out of stock, reward ready, birthdays, new QR customers.
  - Browser notifications.
- **Categories:** chips with counts; search also matches category names.
- **Sales:** date-range presets plus custom dates, KPI tiles, revenue area chart, top-products bar chart (`/analytics/sales` with day/week/month granularity).
- **Loyalty card:** colour picker (`theme.card`). Logo and colour propagate to SadakatKarti via the webhook.

## Open risks / TODO (not started)
1. **Backups:** a daily logical backup exists (local-only module, see `.local/ADMIN.md`); Railway volume backups are still not configured.
2. **Webhook loss:** at-most-once delivery. Add an outbox table with retries; SadakatKarti should dedupe by `id`.
3. **Restarts and scaling:** the rate limiter and admin lockout are in memory, reset on restart and are not shared across replicas. Use Redis if scaling.
4. **Logo size:** since 2026-10-27 the logo is sent only in `business.created` and in `business.updated` when the logo itself changed (`logo_changed`); SadakatKarti's webhook body limit was raised from 20 KB to 600 KB (`E:\SadakatKarti\src\server\app.ts`). A logo over ~440 KB raw (~600 KB base64) still gets 413 on change; SadakatKarti then picks it up on its next lookup. The public business response still includes the full logo.
5. **Notifications cost:** each open tab triggers a full scan every 60 s, and the birthday check loads all customers into Python.
6. **Birthday timezone:** birthdays use the server's UTC `date.today()` instead of the business timezone.
7. **IP filter mismatch:** the frontend admin IP check is exact-match while the backend uses CIDR.
8. **Migrations:** they run in the container CMD, which is unsafe if replicas are ever increased.
9. **Frontend gaps:** no frontend tests and no CSP header. The `zod` npm advisory remains (zod is unused).
10. **Test data in production:**
    - Businesses: "Smoke Test", "QR Test Kafe" (card colour #b93a6b).
    - Customers: "Webhook Test", "Aysel Test".
    - Category: "Qəhvə dənələri".
    - Products: "Arabica 1kq", "Kağız stəkan".
    - One sale.

## Resuming
- Read this file and `README.md` first.
- **Local run:** `start-backend.ps1` and `start-frontend.ps1` (port 3000 may be taken; 3020 was used).
- **Tests:** `cd backend && uv run pytest`.
- **Known nuisance:** the Render plugin's PostToolUse hook errors on every Write/Edit because of the space in the Windows username. It is harmless.
