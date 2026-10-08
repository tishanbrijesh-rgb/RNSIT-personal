# ECDAT — Enterprise Cryptographic Discovery & Analysis Tool

ImpactX 2026

ECDAT scans local repositories, detects cryptographic assets (algorithms, certificates, keys), and produces a risk-ranked inventory with PQC migration guidance.

## Architecture

```
ECDAT-ImpactX/
├── dashboard/       ← React + Vite frontend (port 3000)
├── backend/         ← FastAPI + SQLAlchemy backend (port 8000)
│   ├── routers/     ← API routes (scan, assets, audit, auth, dashboard, outputs)
│   ├── services/    ← Scanner runner, risk engine, correlator, evaluation
│   ├── models/      ← SQLAlchemy ORM models
│   ├── schemas/     ← Pydantic response/request schemas
│   ├── middleware/   ← Rate limiting
│   └── main.py      ← App entrypoint
├── scanner/         ← Repo-tree walker + crypto-pattern detection engine
└── test-repo/       ← Sample repository for demo scans
```

## Quickstart

```bash
# 1. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r backend/requirements.txt

# 3. Set required env vars (or copy .env.example)
export ECDAT_TOKEN_SECRET="your-64-char-secret-here"
export ECDAT_USERS_JSON='{"admin":{"password":"SecurePass1234567890","role":"admin"}}'
# Local development only; production must configure ECDAT_ALLOWED_SCAN_ROOTS instead.
export ECDAT_ALLOW_UNRESTRICTED_SCAN_ROOTS="true"

# 4. Run backend
python -m uvicorn backend.main:app --reload --port 8000

# 5. Run frontend (separate terminal)
cd dashboard && npm install && npm run dev
```

Open http://localhost:3000 — sign in with the credentials from `ECDAT_USERS_JSON`.

## ImpactX Docker launch (Windows)

Prerequisite: Docker Desktop with Docker Compose v2.

```powershell
# First launch builds images, creates private demo credentials, migrates PostgreSQL,
# waits for health checks, and starts the dashboard.
.\scripts\start_impactx.ps1 -Rebuild

# Later launches can reuse the built images and persistent database.
.\scripts\start_impactx.ps1
```

The first launch prints the generated `admin` password once and stores secrets in the gitignored
`.env.impactx` file. Open http://127.0.0.1:3000. The containerized scanner intentionally sees only
the read-only bundled `test-repo`; use the three fixtures in `demo-repositories/` for native demo
checks, or explicitly add a read-only Compose mount and allowlisted container path when presenting
another repository.

```powershell
# Stop services without deleting PostgreSQL evidence/history.
docker compose --env-file .env.impactx down

# Check service state and readiness.
docker compose --env-file .env.impactx ps
Invoke-RestMethod http://127.0.0.1:8000/ready
```

Do not run `down --volumes` unless you intentionally want to delete the persisted ImpactX database.

## Environment Variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ECDAT_TOKEN_SECRET` | Yes | — | HMAC signing key for demo sessions (min 32 chars) |
| `ECDAT_USERS_JSON` | Yes | — | JSON map of `{username: {password, role}}` accounts |
| `ECDAT_CORS_ORIGINS` | No | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated allowed origins |
| `ECDAT_ALLOWED_SCAN_ROOTS` | No | — | Path-separator-delimited allowed scan directories |
| `ECDAT_ALLOW_UNRESTRICTED_SCAN_ROOTS` | Local only | `false` | Explicitly allow arbitrary local scan paths when no root allowlist is configured |
| `ECDAT_REQUEST_TIMEOUT` | No | `120` | Per-request timeout in seconds |
| `ECDAT_AUTO_CREATE_TABLES` | No | `true` | Auto-create DB tables on startup |
| `ECDAT_RATE_LIMIT` | No | `10` | Max requests per minute per client |

## Roles

| Role | Permissions |
|---|---|
| `admin` | Full access including audit log purge |
| `security_analyst` | Create scans, update assets, view all data |
| `auditor` | Read-only access to scans, assets, and audit logs |
| `viewer` | Read-only access to scans and assets |

## API Overview

| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | Public | Issue demo session token |
| POST | `/api/scan` | Write | Create and start a new scan |
| GET | `/api/scans` | Write | List scan jobs (paginated) |
| GET | `/api/scans/{id}` | Write | Get single scan job |
| POST | `/api/scans/{id}/cancel` | Write | Cancel in-progress scan |
| GET | `/api/scans/{id}/events` | Write | SSE progress stream |
| GET | `/api/assets` | Write | List discovered assets (paginated, filterable) |
| GET | `/api/assets/{id}` | Write | Get single asset |
| PATCH | `/api/assets/{id}` | Write | Update asset metadata |
| GET | `/api/reports/risk` | Write | Risk report summary |
| GET | `/api/cbom` | Write | Crypto Bill of Materials |
| GET | `/api/dashboard/summary` | Write | Aggregated dashboard data |
| GET | `/api/evaluation` | Write | Precision/recall/F1 evaluation |
| GET | `/api/audit-logs` | Admin, Auditor | Audit history (paginated) |
| DELETE | `/api/audit-logs/retention/purge` | Admin | Purge old audit entries |
| GET | `/api/outputs/*` | Write | Scanner output files |
| GET | `/health` | Public | Liveness check |
| GET | `/ready` | Public | Readiness check (DB + schema) |

## Development

```bash
# Backend tests
python -m pytest tests/ backend/tests/ -q

# Frontend lint + build
cd dashboard && npm run lint && npm run build
```

## License

MIT — ImpactX internal use.
