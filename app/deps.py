from datetime import datetime, timedelta, timezone
import time
from typing import Annotated, Optional

from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY
from app.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"
_USER_CACHE_TTL_SECONDS = 3.0
_user_cache: dict[str, tuple[float, dict]] = {}


def invalidate_user_cache(user_id: str | None = None) -> None:
    """Clear cached authentication records after account or permission changes."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(str(user_id), None)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(sub: str, role: str, employee_id: str, department_id: Optional[str]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": sub,
        "role": role,
        "employee_id": employee_id,
        "department_id": department_id,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user_optional(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
):
    if creds is None or creds.credentials is None:
        return None
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            return None
    except JWTError:
        return None
    db = get_db()
    try:
        oid = ObjectId(uid)
    except Exception:
        return None
    now = time.monotonic()
    cached = _user_cache.get(str(oid))
    if cached and now - cached[0] < _USER_CACHE_TTL_SECONDS:
        return dict(cached[1])
    user = await db.users.find_one({"_id": oid})
    if not user:
        _user_cache.pop(str(oid), None)
        return None
    user["_id"] = str(user["_id"])
    if user.get("department_id"):
        user["department_id"] = str(user["department_id"])
    _user_cache[str(oid)] = (now, dict(user))
    # Prevent an unbounded cache on long-lived instances with many occasional users.
    if len(_user_cache) > 2000:
        cutoff = now - _USER_CACHE_TTL_SECONDS
        for key, (stored_at, _) in list(_user_cache.items()):
            if stored_at < cutoff:
                _user_cache.pop(key, None)
    return dict(user)


async def get_current_user(user=Depends(get_current_user_optional)):
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_roles(*roles: str):
    async def _inner(user=Depends(get_current_user)):
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _inner
