# RotaShift — deployment and scaling

## Architecture (scalable)

- **Stateless API**: JWT in `Authorization` header; any app instance can serve a request. Run **multiple replicas** behind a load balancer.
- **Shared data**: **MongoDB** is the single source of truth. Use a **replica set** (Atlas, self-hosted, or cloud managed) for availability and read scaling.
- **Connection pool**: Tuned via `MONGO_MAX_POOL_SIZE` (and optional `MONGO_MIN_POOL_SIZE`) per process. With N replicas, total connections ≈ N × pool size — size the cluster limits accordingly.
- **Static UI** is served by the same FastAPI app (`/static`, `/`). For very high traffic, you can put a CDN in front or move static files to object storage + CDN later without changing the API contract.

## Health checks

- `GET /health/live` — process is running (use for **liveness**).
- `GET /health/ready` — MongoDB responds (use for **readiness** / traffic).

## Configuration

See `.env.example`. Important production variables:

| Variable | Purpose |
|----------|---------|
| `ROTASHIFT_ENV` | Set to `production` to enforce a non-default `ROTASHIFT_SECRET_KEY`. |
| `ROTASHIFT_SECRET_KEY` | Strong random secret for JWT signing (e.g. `openssl rand -hex 32`). |
| `MONGO_URI` | Connection string (SRV for Atlas, or `mongodb://host:27017`). |
| `MONGO_MAX_POOL_SIZE` | Per-process pool cap (default `50`). |
| `CORS_ORIGINS` | Comma-separated allowed browser origins if the SPA is on another domain. |

## Docker (single host)

```bash
docker compose up --build
```

Open `http://localhost:8000`. Set secrets in a `.env` file next to `docker-compose.yml` or export variables before `docker compose up`.

## Docker image only

```bash
docker build -t rotashift:latest .
docker run --rm -p 8000:8000 \
  -e MONGO_URI=mongodb://host.docker.internal:27017 \
  -e ROTASHIFT_SECRET_KEY="$(openssl rand -hex 32)" \
  rotashift:latest
```

## Horizontal scaling

1. Point every replica at the **same** `MONGO_URI` (replica set URI recommended).
2. Use the **same** `ROTASHIFT_SECRET_KEY` on all instances so JWTs validate everywhere.
3. Put **nginx**, cloud LB, or Kubernetes **Ingress** in front; enable **sticky sessions** only if you add server-side sessions later (currently not required).
4. Scale pods/replicas based on CPU or request rate; use `/health/ready` for Kubernetes readiness probes.

## Process model

- Default container command uses **one uvicorn worker** per replica (simple, async-friendly).
- For a **single large VM**, you can instead run multiple workers with Gunicorn + Uvicorn worker class (each worker is a separate process with its own Mongo pool):

  ```bash
  pip install gunicorn
  gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000 --proxy-headers
  ```

Prefer **multiple containers** over many Gunicorn workers unless you cannot run orchestration.

## Extend the application

- **New HTTP surface**: add a router under `app/routers/` and `include_router` in `app/main.py`.
- **New collections**: access via `get_db()`; add indexes in `app/seed.py` `ensure_indexes_and_seed`.
- **Background jobs**: introduce a worker service (Celery, RQ, or cloud queues) later; keep API stateless.
