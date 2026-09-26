# Pilot Operations Runbook

## Before deployment

Set `ENVIRONMENT=production`, `DEBUG=false`, a unique 32+ character
`JWT_SECRET_KEY`, a managed Postgres `DATABASE_URL`, Redis `REDIS_URL`, and
the deployed web application `ALLOWED_ORIGINS`. The API now refuses unsafe
production defaults at startup. Run Alembic migrations before serving traffic.
Production databases are intentionally not seeded with pilot accounts; create
the initial Super Admin through the approved provisioning process.

Check `GET /health` for process liveness and `GET /ready` for database, Redis,
and migration readiness. A non-200 readiness response means do not route
traffic to that instance.

## Backup and restore

Take an encrypted Postgres backup before deployment and daily during the pilot.
Retain backups according to the business retention policy and test a restore to
an isolated database monthly. Restore only after stopping writes, recording the
incident time, and obtaining owner approval; verify ledger totals and login
before reopening access.

## Rollback and incident response

If the release fails, stop new writes, preserve application logs, restore the
previous application version, and run its compatible migration procedure. Do
not edit stock or journal rows to repair data: investigate through the
append-only ledgers and record any corrective transaction normally. Rotate
credentials after suspected exposure and notify the pilot owner of material
outages or data-risk incidents.
