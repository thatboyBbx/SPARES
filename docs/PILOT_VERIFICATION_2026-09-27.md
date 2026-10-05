# Pilot verification record — 27 September 2026

## Launch update — 5 October 2026

- Private Sites deployment published successfully at
  `https://sparepilot-operations.panashe-bobojani.chatgpt.site`.
- The Sites build contains the web interface, role-aware API, private sign-in,
  durable D1 storage, health/readiness routes, and schema migration.
- Local release checks passed: 76 backend tests; 9 frontend tests; frontend
  lint; frontend production build; Sites production build.
- Offline replay no longer deletes a transaction after repeated failures and
  no longer treats validation/permission rejections as offline successes.
- Bootstrap data is now scoped by operator role and assigned branch.
- Remaining launch activity is controlled pilot UAT on the invited users'
  actual devices, recorded below.

This record separates automated evidence from checks that still require the
real staging environment. A green local build is not approval to route pilot
traffic.

## Automated evidence

| Area | Evidence | Result |
| --- | --- | --- |
| Frontend interaction tests | `cd apps/web && npm test` | Pass: 2 files, 7 tests |
| Frontend lint | `cd apps/web && npm run lint` | Pass |
| Frontend production build | `cd apps/web && npm run build` | Pass, including PWA service worker |
| Backend suite | `cd apps/api && pytest -q` | Pass: 75 tests |
| Local process health | `GET http://127.0.0.1:8000/health` | 200, development process live |
| Local dependency readiness | `GET http://127.0.0.1:8000/ready` | Expected local failure: Redis and migration checks unavailable |

The frontend suite covers permission-aware navigation, keyboard-driven custom
selects, confirmation-dialog focus trapping and restoration, disabled/busy
confirmation controls, mobile-drawer permission filtering, Escape-to-close,
offline queueing, reconnect replay exactly once, and stale-version conflict
recovery.

The backend suite covers authentication and unauthorized access; owner,
store-keeper, cashier, shop-manager, accountant, and super-admin business
rules; receiving, sales, transfers, purchasing, approvals, expenses,
accounting, notifications, reports, and replenishment; idempotent replays;
stale-version 409 conflicts; readiness; and migration round trips.

## Browser evidence

- 320 × 700: no document-level horizontal overflow; compact header and mobile
  menu are present.
- Mobile menu: opens as a modal dialog, closes with Escape, and returns focus
  to the Menu button.
- 768 × 900 catalogue: no document-level horizontal overflow; search, stock
  filter, and stock data remain available.
- Browser console: no error-level messages during the responsive catalogue
  walkthrough.
- The temporary viewport override was reset after verification.

## Role and workflow matrix

| Workflow | Automated gate | Staging UAT still required |
| --- | --- | --- |
| Owner | Admin users/branches, reports, approvals, replenishment | Review live pilot dashboards and approve launch |
| Store keeper | Receipt, purchase receipt, fulfil/receive transfer, role restrictions | Scan the movement ledger after real-device transactions |
| Cashier | Customer/walk-in sale, idempotency, forbidden admin/accounting/transfer actions | Complete a real counter-sale walkthrough |
| Shop manager | Transfer request/receipt and report restrictions | Complete transfer handoff on the intended phone/tablet |
| Accountant | Expense approval, supplier payment, journal entries | Reconcile pilot opening totals and payment method labels |
| Offline/reconnect | IndexedDB queue, exactly-once replay | Disconnect a pilot device, transact, reconnect, and inspect sync status |
| Conflict recovery | 409 sync issue retained for recovery | Reproduce a stale transfer on two staging devices and resolve it |
| Request failure | Error paths preserve recoverable local state | Verify API outage messaging against the staging hostname |

## Staging release gate

The repository is ready to build for staging, but no staging host, managed
Postgres URL, Redis URL, deployment credentials, or approved public origin is
available in this checkout. Do not claim a staging deployment until all of the
following are recorded:

- [ ] Configure `ENVIRONMENT=production`, `DEBUG=false`, a unique 32+ character
      `JWT_SECRET_KEY`, managed Postgres `DATABASE_URL`, Redis `REDIS_URL`, and
      the exact `ALLOWED_ORIGINS` value.
- [ ] Run Alembic migrations against the staging database.
- [ ] Deploy the API and the production web build.
- [ ] Confirm staging `/health` returns 200.
- [ ] Confirm staging `/ready` returns 200 with database, Redis, and migrations
      all ready. A 503 is a release blocker.
- [ ] Provision initial administration access through the approved process;
      never seed pilot credentials in production mode.
- [ ] Execute every unchecked item in `PILOT_UAT_CHECKLIST.md` and record tester,
      device, timestamp, and outcome.

## Backup and rollback gate

- [ ] Take an encrypted Postgres backup immediately before deployment and
      record its identifier, timestamp, retention class, and checksum.
- [ ] Restore that backup into an isolated database.
- [ ] Verify login, stock totals, movement ledger totals, and journal totals on
      the restored copy.
- [ ] Record the last known compatible application version and migration
      revision.
- [ ] Rehearse stopping writes, preserving logs, restoring the prior version,
      applying its compatible migration procedure, and reopening only after
      readiness and ledger checks pass.
- [ ] Obtain owner approval for the pilot launch.

The local readiness response on 27 September 2026 was `503 not_ready` because
Redis and migration readiness were unavailable. That is acceptable for this
development instance, but it demonstrates that the production gate correctly
blocks traffic when dependencies are incomplete.
