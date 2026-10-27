# Hesabatım architecture and implementation plan

The knowledge base at `E:\ZapcasSatSecondBrain\ZapcastSecondBrain` was read, never changed. Relevant sources: ADR-001 Deterministic Service Layer, ADR-002 Append-Only Inventory Ledger, ADR-003 Human Confirmation for AI Writes, ADR-004 LLM Provider Abstraction with Offline Fallback, ADR-007 RLS as Defense in Depth, ADR-008 Decimal Money Representation; Multi-Tenancy, Database Transactions, Password Hashing and the ZapcasSat technical-debt review.

## Decisions

* Modular monolith: Next.js/TypeScript UI → FastAPI → application services → SQLAlchemy/PostgreSQL. SQLite is explicitly a local development mode. Existing incomplete inventory services are retained and tested; unrelated marketplace source and tests are preserved under `archive/` outside the active application.
* Business membership is checked at API and AI execution boundaries. Model arguments cannot supply a tenant. No public database API is exposed. RLS from the Supabase-specific reference is not copied blindly; application authorization is the isolation boundary.
* Every stock write and corresponding ledger event share a transaction. PostgreSQL row locks serialize updates; SQLite BEGIN IMMEDIATE serializes local writers. Sales and refunds snapshot cost. Unknown cost is never assumed zero for total profit.
* Write tools produce persisted pending previews. Confirmation revalidates and atomically claims an action. Exact affirmative chat messages confirm the latest presented pending batch; arbitrary model output cannot confirm. REST mutations use idempotency keys; AI uses action identity.
* Money uses Decimal/Numeric and strings across the API. Business-local dates determine reports. Returns reduce revenue on their return date and preserve original sales.
* Browser sessions use HttpOnly cookies, SameSite=Lax and Origin checking; production requires HTTPS and a strong JWT secret. Account token versions revoke sessions. Bearer authentication remains available for API integrations.
* Providers are configured only on the backend. Without a key, the UI explicitly identifies the limited deterministic Azerbaijani parser. No invented dashboard numbers or seeded transactions.

## Execution plan

1. Read architectural references and audit existing code/tests.
2. Complete authentication, migrations, transaction safeguards and AI confirmation/calendar behavior.
3. Replace marketplace UI with assistant, dashboard, inventory, sales, analytics and settings, connected to real endpoints.
4. Exercise authentication, isolation, stock, sales, rollback, duplicate and concurrent requests; compile frontend and test the user journey.
5. Document environment setup, operational procedures and verified limitations.
