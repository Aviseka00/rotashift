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
