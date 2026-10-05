# SPOP — Phased Delivery Plan

## Phase 0 — Foundations ✅ (this scaffold)
Repo structure, Docker Compose (Postgres/Redis/API/web), Alembic wired to
SQLAlchemy models, the `SyncedEntityMixin` base for every future
sync-aware table, CI (lint + type-check + test), and a Vite PWA scaffold
with Workbox + Dexie wired but empty.

**Exit criteria:** `docker compose up`, hit `/health`, load a blank PWA
that installs on a phone. **Met** — see README for verification steps.

## Phase 1 — Identity, RBAC, Branches
User model + fixed role enum (Owner, Accountant, Store Keeper, Shop
Manager, Cashier, Super Admin), JWT auth + refresh tokens, device
registration, branch/warehouse model as a list (not hardcoded fields),
permission middleware, minimal admin screens.

## Phase 2 — Inventory Engine
Product/category/supplier models, append-only stock ledger (never a
mutable quantity column), current-stock aggregation strategy decided
explicitly, transfer workflow (shop requests → warehouse fulfils).

## Phase 3 — Offline Sync
Pulled forward deliberately, right after inventory and before
sales/purchasing — retrofitting sync onto three modules at once is
harder than building it against one. Dexie outbox pattern, batch sync on
reconnect, explicit conflict resolution strategy, server-side idempotency
for retried submissions.

## Phase 4 — Sales Recording
Sales entries writing `-N` ledger events, customer model, daily
sales views.

## Phase 5 — Purchasing
Supplier orders, goods receiving (`+N` ledger events), purchase requests.

## Phase 6 — Approval Engine
Generic approval request table (type, priority, requester, approver,
status, deadline) reused across transfers/adjustments/expenses/price
changes, with escalation rules.

## Phase 7 — Notifications
FCM push (approvals, low stock, failed sync, transfer confirmations),
email fallback.

## Phase 8 — Accounting ✅
Expenses, supplier payments, journals — deliberately last, depends on
stable sales + purchasing data. A single append-only `journal_entries`
ledger (signed amounts, same discipline as the stock ledger) is posted to
automatically from sale, expense-approval, purchase-order-receive, and
supplier-payment — never written directly by a client.

## Phase 9 — Reports & Analytics ✅
Owner/Shop Manager dashboards built as literal answers to the operating
questions an owner actually asks: what did we make (profit-and-loss),
which branches are pulling their weight (branch performance), what are we
spending on (expenses summary), and where are approvals stuck
(approvals-summary, alongside the existing stock-health and daily-sales
views).

## Phase 10 — AI / Predictive (heuristic foundation shipped; real ML still deferred)
Replenishment prediction, demand forecasting. This phase's own rationale
was to wait for real production data before fitting a model — that still
holds, so no ML/forecasting library was introduced. What shipped instead
is a deterministic, fully-explainable v1: trailing 30-day sales velocity
projected against current stock, surfaced at
`/api/v1/reports/replenishment-suggestions`. It's upgradeable to a real
model later without changing the response shape callers depend on.

## Phase 11 — Pilot Readiness, UAT, and Deployment Hardening ✅
Production startup rejects unsafe defaults, pilot accounts are never seeded in
production, and `/ready` reports database, Redis, and migration dependency
state. The pilot runbook and role-based UAT checklist live under `docs/`.
