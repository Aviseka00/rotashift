from motor.motor_asyncio import AsyncIOMotorClient

from app.config import (
    DB_NAME,
    MONGO_MAX_POOL_SIZE,
    MONGO_MIN_POOL_SIZE,
    MONGO_SERVER_SELECTION_TIMEOUT_MS,
    MONGO_URI,
)

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        kwargs = {
            "serverSelectionTimeoutMS": MONGO_SERVER_SELECTION_TIMEOUT_MS,
            "maxPoolSize": MONGO_MAX_POOL_SIZE,
        }
        if MONGO_MIN_POOL_SIZE > 0:
            kwargs["minPoolSize"] = MONGO_MIN_POOL_SIZE
        _client = AsyncIOMotorClient(MONGO_URI, **kwargs)
    return _client


def get_db():
    return get_client()[DB_NAME]
