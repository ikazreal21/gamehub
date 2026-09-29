"""Simple JWT auth for single admin user. Put behind HTTPS in production."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import config

security = HTTPBearer(auto_error=False)


def _hash(password: str) -> str:
    return hashlib.sha256(f"{config.ADMIN_USER}:{password}".encode()).hexdigest()


_STORED_HASH = _hash(config.ADMIN_PASS)


def verify_login(username: str, password: str) -> bool:
    if not secrets.compare_digest(username, config.ADMIN_USER):
        return False
    return secrets.compare_digest(_hash(password), _STORED_HASH)


def create_token() -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=config.TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": config.ADMIN_USER, "exp": exp}, config.SECRET_KEY, algorithm="HS256")


def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(security)):
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, config.SECRET_KEY, algorithms=["HS256"])
        if payload.get("sub") != config.ADMIN_USER:
            raise HTTPException(status_code=401, detail="Invalid token")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
