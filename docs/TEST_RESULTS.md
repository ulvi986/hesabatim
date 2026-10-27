# Verification results

Verified 18 October 2026, local Windows workspace.

* SQLite backend suite: **85 passed**, 1 upstream httpx/Starlette deprecation warning.
* PostgreSQL 17 backend suite: **85 passed**, 2 deprecation warnings (the same upstream warning and Alembic's path separator compatibility notice).
* `alembic check` against the migrated PostgreSQL test database: **No new upgrade operations detected.** The disposable database was explicitly named `hesabatim_test`; the fixture rejects any destructive PostgreSQL test URL whose database name does not end in `_test`.
* `npm run build`: **passed**; production Next.js 16 build and TypeScript checking completed, with `/` and `/_not-found` prerendered.
* Chromium smoke test: **passed**, using Node 22's built-in WebSocket/CDP client and the installed Chromium. Verified registration, HttpOnly browser session, product preview without writes, Azerbaijani multi-product confirmation, AI sale, stock decrement, actual revenue, dashboard data, manual product creation, manual sale, full cancellation/refund, settings access, dark theme and mobile layout without horizontal overflow or uncaught browser exceptions.
* Screenshots: `docs/screenshots/assistant-desktop.png`, `confirmation-desktop.png`, `dashboard-desktop.png`, `inventory-desktop.png`, `settings-dark.png`, `assistant-mobile.png`.

Browser tests and screenshots used a separate local SQLite database. PostgreSQL tests and concurrent sale/stock/AI-confirmation tests ran against a separate local PostgreSQL 17 cluster. No external LLM key was available; real third-party provider behavior was not tested. The assistant's development parser and full preview/confirmation pipeline were tested offline.
