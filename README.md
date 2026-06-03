# RotaShift

Web app for department shift rosters, leave requests, and manager/admin approvals (FastAPI + MongoDB + static UI).

**Repository:** `https://github.com/Aviseka00/rotashift.git`

## Work from any PC (Git clone)

Use this flow on a new laptop, office PC, or home machine. Your code and settings travel via Git; database data stays in **MongoDB** (Atlas or a shared cluster), not in the repo.

### Prerequisites

| Tool | Purpose |
|------|---------|
| [Git](https://git-scm.com/downloads) | Clone and pull the project |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Run API + optional local Mongo (Windows/Mac/Linux) |

### One-time setup on a new machine

```bash
git clone https://github.com/Aviseka00/rotashift.git
cd rotashift
```

1. **Environment file** — scripts create `.env` from `.env.example` on first run, or do it manually:

   ```bash
   cp .env.example .env    # Mac/Linux
   copy .env.example .env  # Windows CMD
   ```

2. **Shared database (recommended)** — edit `.env` and set `MONGO_URI` to your **MongoDB Atlas** connection string (same URI on every PC = same users and rosters). Set `ROTASHIFT_DB=rotashift` if needed.

3. **Secrets** — never commit `.env`. Store Atlas password and `ROTASHIFT_SECRET_KEY` in a password manager or team vault.

### Start the app

**Windows:** double-click `start-docker.bat` or:

```powershell
.\scripts\dev-start.ps1
```

**Mac / Linux:**

```bash
chmod +x start-docker.sh scripts/dev-start.sh
./start-docker.sh
```

**Manual (any OS):**

```bash
docker compose up -d --build
```

Open **http://localhost:8000**. Check data connection: **http://localhost:8000/api/meta/registration** (`user_count`, `mongo_cluster_host`).

| Action | Command |
|--------|---------|
| Stop | `docker compose down` |
| View logs | `docker compose logs -f api` |
| Pull latest code | `git pull` then `docker compose up -d --build` |

**Default dev invite codes** (when not overridden in `.env`): manager `MANAGER-DEV-2026`, admin `ADMIN-DEV-2026`.

### Python-only (no Docker)

Requires Python **3.12+** and a reachable MongoDB (`MONGO_URI` in `.env`).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or on Windows run `start.bat` (uses system Python, not Docker).

### What is in Git vs what is not

| In Git | Not in Git (per machine) |
|--------|---------------------------|
| App code, UI, `Dockerfile`, `docker-compose.yml`, `.env.example` | `.env` (passwords, `MONGO_URI`) |
| `render.yaml` for cloud deploy | Uploaded activity images (server temp dir) |
| Tests, CI workflow | Local Docker volume `mongo_data` (empty demo DB only) |

---

## Where user data is in MongoDB

- The app uses the database named **`ROTASHIFT_DB`** (default: **`rotashift`**), **not** necessarily the database name in your `MONGO_URI` path. Many Atlas strings look like `...mongodb.net/test` — you must still open the **`rotashift`** database (or whatever you set `ROTASHIFT_DB` to) in **Atlas → Browse Collections**.
- User accounts are in the **`users`** collection (`rotashift.users` in Compass).
- To confirm what the running server uses, open **`GET /api/meta/registration`** on your deployed site and read **`mongo_database`** and **`mongo_users_collection`**, or check Render logs on startup for a line like `database='rotashift'`.
- The same JSON includes **`user_count`**, **`department_count`**, and **`mongo_cluster_host`** (hostname from `MONGO_URI`, no password). After you register once, **`user_count` should go up**. If it stays **0** but Atlas [Data Explorer](https://cloud.mongodb.com/) shows users, **Render’s `MONGO_URI` points at a different cluster** than the one you’re viewing — fix the secret and redeploy. If **`mongo_cluster_host`** matches your cluster but **`user_count`** increases in JSON while Atlas still shows **0** documents, refresh Atlas or clear any **filter** on the `users` collection query bar.

## Share with your team for testing

### Option A — Docker (recommended)

Everyone needs [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/Mac) or Docker Engine + Compose (Linux).

1. Get the code: clone the repo, or unzip a package built with `scripts/package-for-team.ps1` (see below).
2. From the project root:

   ```bash
   docker compose up --build
   ```

3. Open **http://localhost:8000** in a browser.

**Compose defaults (safe for local demos only):**

| Item | Value |
|------|--------|
| Manager signup invite | `MANAGER-DEV-2026` |
| Admin signup invite | `ADMIN-DEV-2026` |
| MongoDB | Started automatically; data persists in a Docker volume |

**First steps for testers:** use **Register**, pick a department name from the list (e.g. `rota`, `cholera`, `malaria`, `shigella`), register as **employee** with no invite code. For **manager** or **admin** self-signup, enter the invite codes above.

To pre-create admin logins on startup, copy `.env.example` to `.env`, set `ROTASHIFT_ADMIN_EMPLOYEE_ID` and `ROTASHIFT_ADMIN_PASSWORD`, then run Compose again (see comments in `.env.example`).

### Option B — Python on your machine

1. Python **3.12+**, MongoDB running locally or use **MongoDB Atlas** and set `MONGO_URI` in a `.env` file (see `.env.example`).
2. Create a venv, install deps, run:

   ```bash
   pip install -r requirements.txt
   python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

3. Open **http://localhost:8000** (or `http://YOUR_LAN_IP:8000` from another device on the same Wi‑Fi).

Invite codes default to the same dev strings as in `app/config.py` unless you override them in `.env`.

### Make a zip to email / Slack (no `.git`, no secrets)

From the repo root, with Git installed:

**Windows (PowerShell):**

```powershell
.\scripts\package-for-team.ps1
```

**Mac / Linux:**

```bash
chmod +x scripts/package-for-team.sh
./scripts/package-for-team.sh
```

This writes **`rotashift-team-test.zip`** in the project root. Share that file plus a link to this README (or paste the Docker section). Recipients unzip, then run `docker compose up --build`.

### Production / cloud

Use your own MongoDB (e.g. Atlas), set strong `ROTASHIFT_SECRET_KEY`, and deploy with the included `Dockerfile` or `render.yaml` as appropriate.

---

## Publish a public URL (so anyone can register and try)

Render’s **free** web tier is enough for demos (the service **sleeps after idle**; the first visit after sleep can take ~30–60s). You still need **MongoDB in the cloud** — the easiest free option is **[MongoDB Atlas](https://www.mongodb.com/cloud/atlas)**.

### 1) MongoDB Atlas

1. Sign up at [Atlas](https://www.mongodb.com/cloud/atlas) → create a **free (M0)** cluster.
2. **Database Access** → add a database user (username + password).  
3. **Network Access** → **Add IP Address** → **Allow access from anywhere** (`0.0.0.0/0`) so Render can connect (required on free Render; tighten later if you move to paid static IPs).
4. **Database** → **Connect** → **Drivers** → copy the **SRV connection string** (`mongodb+srv://...`). Replace `<password>` with your user’s password (URL‑encode special characters in the password if needed).

### 2) Render (connect this GitHub repo)

1. Sign up at [Render](https://render.com) (GitHub login is fine).
2. **New** → **Blueprint** → connect **your GitHub repo** (this project) → apply the repo’s **`render.yaml`**.  
   *Or:* **New** → **Web Service** → same repo → **Runtime: Python 3**, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips '*'`, health check path **`/health/live`**.
3. Open the new **Web Service** → **Environment** and set:

| Variable | Example / notes |
|----------|------------------|
| `MONGO_URI` | Your Atlas `mongodb+srv://...` string (mark **Secret**). |
| `ROTASHIFT_SECRET_KEY` | Long random string, e.g. run locally: `openssl rand -hex 32` ( **Secret** ). Required because `ROTASHIFT_ENV` is `production` in `render.yaml`. |
| `ROTASHIFT_REGISTER_CODE_MANAGER` | Any secret phrase testers need for **manager** self‑registration ( **Secret** ). |
| `ROTASHIFT_REGISTER_CODE_ADMIN` | Any secret phrase for **admin** self‑registration ( **Secret** ). |

4. **Save** → wait for **Deploy** to succeed → open the URL Render shows (e.g. `https://rotashift.onrender.com`).

**Share with testers:** the Render URL, the **manager** and **admin** invite codes you chose, and that **employees** can use **Register** with role **employee**, no code, and a department like **`rota`** (seeded on first app start).

**Optional env (same Environment page):**

- `ROTASHIFT_ADMIN_EMPLOYEE_ID` + `ROTASHIFT_ADMIN_PASSWORD` — bootstrap or promote admin users (see `.env.example`).
- `CORS_ORIGINS` — only if the UI is hosted on a **different** domain than the API; same Render hostname + default static files **do not** need CORS changes.

### 3) If the deploy fails

- Logs in Render → often **`MONGO_URI`** wrong or Atlas firewall blocking.
- Logs mentioning **secret key** → set `ROTASHIFT_SECRET_KEY` to a non‑placeholder value.

### 4) If registration still fails after deploy

- **Redeploy** after pulling the latest `main` (fixes for sign-up + service worker are in the app).
- Ask testers to **hard-refresh** or **clear site data** for your Render URL (an old service worker or `localStorage` token from a previous deploy can block the department list).
- **Employee** signup needs a real department name (e.g. `rota`); **manager/admin** need the invite codes you set in Render.

### Other hosts

The **`Dockerfile`** works on any container host (Fly.io, Railway, Google Cloud Run, etc.) with `PORT` set and the same environment variables as above.
