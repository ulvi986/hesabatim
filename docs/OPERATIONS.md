# Operations

Use PostgreSQL for production. The application rejects production SQLite. Keep the database on a private network; do not expose it through a public SQL/PostgREST endpoint. The backend enforces business membership and resource ownership. No RLS policy is installed: application authorization is the current tenant isolation boundary.

## Release

1. Restore-test a recent backup and take a pre-release backup.
2. Install exact Python dependencies from `uv.lock` and JavaScript dependencies with `npm ci`.
3. Run `uv run alembic upgrade head` as a single release job, before application workers start. Do not run simultaneous migrations from multiple replicas.
4. Build the frontend with `API_BACKEND_URL` pointing to the reachable backend URL, then run `npm start` behind HTTPS. Next.js proxies `/api` to FastAPI.
5. Set `ENVIRONMENT=production`, a randomly generated `JWT_SECRET` (48 random bytes recommended), `DATABASE_URL`, and `CORS_ORIGINS` to the exact browser origin, including scheme/port. Cookies become Secure in production. Secrets must stay in the backend environment.
6. Start one backend worker. Configure `TRUSTED_PROXY_IPS` to only your actual reverse proxy addresses; do not use `*`. Verify `/health`, registration/login and a test tenant's preview/confirmation.

Rate limits are in memory, bounded and per process. Horizontal deployment requires a shared limiter (e.g. Redis) or equivalent ingress enforcement before adding workers. AI daily limits are database-backed counts, not a billing-grade concurrent quota reservation. Use an upstream body-size and request-timeout limit, disable proxy buffering for SSE, and retain an idle timeout greater than the configured LLM timeout. No billing integration is provided.

## Backup and restore

Use PostgreSQL managed automated backups plus WAL/PITR where available. Suggested starting policy: daily encrypted backups, 30-day retention, monthly recovery exercises; determine business-specific RPO/RTO before launch. Restrict backup credentials and storage access.

```powershell
# libpq connection settings (PGHOST/PGPORT/PGUSER/PGDATABASE) and password from your secret manager
pg_dump --format=custom --file=hesabatim-backup.dump
# Restore only to a NEW, empty recovery database, never over a live application database:
createdb hesabatim_recovery
pg_restore --dbname=hesabatim_recovery --no-owner --no-acl hesabatim-backup.dump
```

Verify migration revision, record counts, tenant separation and ledger balances before switching traffic. A useful reconciliation query is stock quantity versus SUM(inventory_events.delta) per product; differences must be investigated, not overwritten. Use an independent backup host and monitoring outside this app. These procedures are documented, not a configured backup service.

## Audit and incident handling

Inventory movements, sales/returns and business mutations persist audit records. Do not delete original sales to correct errors: issue a partial return/full cancellation. Use logout-all or a password change to revoke all previously issued account tokens. Rotate the JWT secret to revoke all sessions globally. Never log API keys, passwords, authorization headers, uploaded content or chat prompts. Application logs include request IDs; configure log shipping and alerting in your hosting environment.

CSV/XLSX import defaults to preview. Confirmation applies valid rows with savepoints and reports per-row errors; this is not an all-or-nothing batch. Existing SKU/name matches set stock to the supplied absolute quantity. Export text cells are escaped against spreadsheet formula execution. Import is limited to 5 MB and 2,000 rows by the service. Test with a backup before large imports.

## Scope and scaling limits

Inventory adjustments represent received goods or stock corrections; there is no supplier invoicing, purchase-order, accounts-payable, tax or expenses ledger. Gross profit is not net profit. Purchase cost is the product's recorded unit cost at sale time, not FIFO/weighted-average valuation. Reports currently aggregate bounded date ranges in application memory; move high-volume aggregation into SQL before scaling. Conversations and idempotency records currently require an operator retention policy. Email verification, password recovery, MFA, business-member invitations and subscription billing are not implemented.
