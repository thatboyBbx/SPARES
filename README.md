# Spare Parts Operations Platform (SPOP)

An offline-first Progressive Web App (PWA) operational ERP for automotive
spare parts SMEs (1 warehouse + N retail shops). Not a POS — tracks
inventory movement, transfers, sales recording, purchasing, approvals,
accounting, and audit trails.

**Current phase: Phase 0 — Foundations.** See `PHASED_PLAN.md` for the
full roadmap.

## Current MVP

The pilot now has database-backed identity, branches, vehicle-fitment
catalogue, supplier receipts, append-only inventory movements, transfers,
sales, approvals, expenses, notifications, and owner reporting. Sample
records live in `apps/api/app/data/sample_data.json` and are inserted only
into an empty database; no sample business records are embedded in the UI.

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
- http://localhost:5173 should show the Phase 0 scaffold page, reporting
  the pilot control room, loaded from the API database.

## Pilot credentials

On first database boot, `owner@sparepilot.local` with password `pilot123`
can exercise the login endpoint. Replace the pilot account before any real
deployment.

## Core MVP API

- `GET /api/v1/bootstrap` loads operational data for the web app.
- `POST /api/v1/receipts`, `/transfers`, and `/sales` post stock workflow events.
- `POST /api/v1/approvals` and `/expenses` capture controls and accounting inputs.
- `GET /api/v1/reports/overview` returns owner metrics and branch stock.

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
