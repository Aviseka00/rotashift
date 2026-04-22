import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    # Load .env from project root first, then cwd (covers different launch directories / IDEs).
    _loaded = False
    for _env_path in (_ROOT / ".env", Path.cwd() / ".env"):
        if _env_path.is_file():
            load_dotenv(_env_path, encoding="utf-8")
            _loaded = True
            break
    if not _loaded:
        load_dotenv(encoding="utf-8")
except ImportError:
    pass


def _registration_code(var_name: str, dev_default: str) -> str:
    """Non-empty invite code. Uses dev_default when the variable is unset (e.g. .env missing)."""
    raw = os.getenv(var_name)
    if raw is None:
        return dev_default
    s = raw.strip().strip('"').strip("'")
    return s if s else dev_default


DEFAULT_PLACEHOLDER_SECRET = "change-me-in-production-use-openssl-rand-hex-32"
SECRET_KEY = os.getenv("ROTASHIFT_SECRET_KEY", DEFAULT_PLACEHOLDER_SECRET)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("ROTASHIFT_DB", "rotashift")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))  # 7 days

# development | staging | production — affects strict checks on boot
ROTASHIFT_ENV = os.getenv("ROTASHIFT_ENV", "development").strip().lower()

# Comma-separated origins for browser API access (e.g. https://app.example.com). Empty = same-origin only.
CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "").strip()

# MongoDB driver tuning (scale connection pool with app replicas × expected concurrency)
MONGO_MAX_POOL_SIZE = int(os.getenv("MONGO_MAX_POOL_SIZE", "50"))
MONGO_MIN_POOL_SIZE = int(os.getenv("MONGO_MIN_POOL_SIZE", "0"))
MONGO_SERVER_SELECTION_TIMEOUT_MS = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))

# Manager / admin invite codes (override via env or .env). Defaults always enable self-registration locally.
REGISTER_CODE_MANAGER = _registration_code("ROTASHIFT_REGISTER_CODE_MANAGER", "MANAGER-DEV-2026")
REGISTER_CODE_ADMIN = _registration_code("ROTASHIFT_REGISTER_CODE_ADMIN", "ADMIN-DEV-2026")

SHIFT_DEFINITIONS = {
    "A": {"label": "A", "start": "06:00", "end": "14:30"},
    "B": {"label": "B", "start": "14:00", "end": "22:30"},
    "C": {"label": "C", "start": "20:00", "end": "06:30", "overnight": True},
    "G": {"label": "G", "start": "09:00", "end": "17:30"},
    "L": {"label": "Leave", "description": "Leave"},
    "WO": {"label": "Week off", "description": "Week off"},
}

# Roster / calendar codes with clock times (employee shift-change requests stay on these only).
TIMED_SHIFT_CODES = frozenset(k for k, v in SHIFT_DEFINITIONS.items() if v.get("start"))

# Canonical roster cell codes (client always merges these into pickers even if an older API omits some).
ROSTER_SHIFT_CODES: tuple[str, ...] = tuple(SHIFT_DEFINITIONS.keys())
