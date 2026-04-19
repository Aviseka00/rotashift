# RotaShift

Web app for department shift rosters, leave requests, and manager/admin approvals (FastAPI + MongoDB + static UI).

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
