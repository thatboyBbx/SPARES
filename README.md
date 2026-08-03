# Spare Parts Operations Platform (SPOP)

An offline-first Progressive Web App (PWA) operational ERP for automotive
spare parts SMEs (1 warehouse + N retail shops). Not a POS — tracks
inventory movement, transfers, sales recording, purchasing, approvals,
accounting, and audit trails.

**Status: Phases 1–7 complete.** Phases 8–10 (Accounting, Reports &
Analytics, AI/Predictive) are not started. See `PHASED_PLAN.md` for the
full roadmap and phase definitions.

## Current MVP

- **Phase 1 — Identity, RBAC, Branches**: six fixed roles (Owner,
  Accountant, Store Keeper, Shop Manager, Cashier, Super Admin), bcrypt
  password hashing with transparent legacy-hash upgrade, JWT access +
  rotating refresh tokens, per-device registration, role-gated admin
  screens for users and branches.
- **Phase 2 — Inventory Engine**: product categories (self-referential),
  append-only stock ledger as the single source of truth, and a two-step
  branch transfer workflow (`requested → in_transit → received`, plus
  cancel) instead of an instant one-shot move.
- **Phase 3 — Offline Sync**: every device-authored table carries
  `id/device_id/created_by_id/created_at/updated_at/version/sync_status`;
  optimistic-concurrency conflict detection (`expected_version` → `409`)
  on transfer fulfil/receive and purchase-order receive; idempotency keys
  on every create endpoint; a real Dexie-backed offline cache/outbox and a
  real Workbox (`injectManifest`) service worker.
- **Phase 4 — Sales Recording**: customer records (optional on a sale,
  walk-ins stay valid), a daily-sales report view.
- **Phase 5 — Purchasing**: purchase orders flow
  `requested → approved → received`, with per-line idempotent receiving.
- **Phase 6 — Approval Engine**: approvals carry a deadline and lazily
  escalate priority when overdue; expense and purchase-order approvals are
  auto-created and resolved through one shared dispatch path so the
  approval and the record it gates can never drift out of sync; gated
  product price changes.
- **Phase 7 — Notifications**: pluggable notification channels (console
  email by default, SMTP and Firebase Cloud Messaging when configured),
  low-stock alerts fired only on the movement that crosses the reorder
  threshold, and a sync-failure reporting endpoint for the offline outbox.

Sample records live in `apps/api/app/data/sample_data.json` and are
inserted only into an empty database; no sample business records are
embedded in the UI.

## Monorepo layout

```
spop/
├── apps/
│   ├── api/          FastAPI backend (Python)
│   └── web/          React 19 + TypeScript PWA frontend (Vite)
├── infra/
│   └── docker-compose.yml
└── .github/workflows/ci.yml
```

## Local development

### Prerequisites
- Docker + Docker Compose
- Node 22+ (only needed if running the frontend outside Docker)
- Python 3.12+ (only needed if running the backend outside Docker)

### Quick start (Docker — recommended)

```bash
cd infra
docker compose up --build
```

This brings up Postgres, Redis, the FastAPI API (http://localhost:8000),
and the Vite dev server (http://localhost:5173) with hot reload on both.

Verify it worked:
- http://localhost:8000/health should return `{"status": "ok", ...}`
- http://localhost:5173 should show the sign-in screen; log in with any
  seeded account below to reach the pilot control room, loaded from the
  API database.

## Pilot credentials

On first database boot, one seeded account per role is available, all
with password `pilot123`:

| Email                          | Role          |
| ------------------------------ | ------------- |
| `owner@sparepilot.local`       | Owner         |
| `warehouse@sparepilot.local`   | Store Keeper  |
| `shop@sparepilot.local`        | Shop Manager  |
| `cashier@sparepilot.local`     | Cashier       |
| `accounts@sparepilot.local`    | Accountant    |
| `admin@sparepilot.local`       | Super Admin   |

Replace all pilot accounts before any real deployment.

## Core MVP API

- `GET /api/v1/bootstrap` loads operational data for the web app.
- `POST /api/v1/auth/login`, `/auth/refresh`, `/auth/logout`; `POST /devices/register`.
- `GET/POST /api/v1/users`, `PATCH /users/{id}`; `GET/POST /branches`.
- `GET/POST /api/v1/categories`; `PATCH /products/{id}/category`; `PATCH /products/{id}/price`.
- `POST /api/v1/receipts` and `/sales` post stock workflow events.
- `POST /api/v1/transfers` (request), `/transfers/{id}/fulfil`, `/transfers/{id}/receive`, `/transfers/{id}/cancel`.
- `POST /api/v1/purchase-orders` (request), `/purchase-orders/{id}/approve`, `/purchase-orders/{id}/receive`.
- `GET/POST /api/v1/customers`.
- `POST /api/v1/approvals`, `/approvals/{id}/approve|reject`; `POST /expenses`, `/expenses/{id}/approve|reject`.
- `GET /api/v1/notifications`; `POST /notifications/push-token`; `POST /sync/report-failure`.
- `GET /api/v1/reports/overview` and `/reports/daily-sales` return owner metrics, branch stock, and sales aggregates.

### Running without Docker

**Backend:**
```bash
cd apps/api
pip install -r requirements.txt --break-system-packages
cp .env.example .env   # adjust DATABASE_URL to a local Postgres instance
alembic upgrade head
uvicorn app.main:app --reload
```

**Frontend:**
```bash
cd apps/web
npm install
npm run dev
```

## Database migrations

Migrations are managed with Alembic, wired to the SQLAlchemy models
under `apps/api/app/models/`. Alembic is authoritative for Postgres —
the Docker Compose `api` service runs `alembic upgrade head` before
`uvicorn` starts on every boot. (`Base.metadata.create_all()` still runs
too, purely as a no-op fallback for the SQLite test suite, which never
runs Alembic.)

```bash
cd apps/api
alembic revision --autogenerate -m "describe your change"
alembic upgrade head
```

## Testing

```bash
# Backend
cd apps/api && pytest

# Frontend
cd apps/web && npx tsc -b && npm run build
```

CI (`.github/workflows/ci.yml`) runs `pytest`, `oxlint`, `tsc -b`, and
`vite build` on Node 22. Locally, `npm run build` and `npm run lint`
require Node ≥20.12 (rolldown/oxlint both need newer `node:util`/ESM
loader behavior than earlier Node 20.x patch releases ship) — `tsc -b`
alone works on any modern Node and is sufficient to type-check.

## Design principles (do not violate without discussion)

- **Offline-first**: every synced entity carries `id (UUID)`, `device_id`,
  `created_by_id`, `created_at`, `updated_at`, `version`, `sync_status` —
  see `apps/api/app/db/base.py` (`SyncedEntityMixin`).
- **Inventory is event-sourced**: never store a mutable stock quantity —
  only append-only movement events (purchase/sale/transfer/return/damage).
  Current stock is always derived from the ledger.
- **Modular monolith, not microservices.** Avoid Kubernetes, Kafka,
  RabbitMQ, GraphQL, CQRS, and Elasticsearch — the architecture doc
  explicitly calls these out as unnecessary complexity for this scale.
