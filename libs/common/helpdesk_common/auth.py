"""Shared offline JWT validation and role dependencies for HelpDesk services."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ISSUER = "helpdesk-auth"
ALGORITHM = "RS256"
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    name: str
    role: str


@lru_cache(maxsize=4)
def _read_public_key(path: str) -> bytes:
    return Path(path).read_bytes()


def decode_access_token(token: str, public_key: str | bytes) -> CurrentUser:
    """Verify an access token's signature, issuer, expiry, and required claims."""
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            public_key,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={
                "require": ["sub", "email", "name", "role", "jti", "iat", "exp", "iss"]
            },
        )
        if claims.get("type", "access") != "access":
            raise jwt.InvalidTokenError("not an access token")
        return CurrentUser(
            id=str(claims["sub"]),
            email=str(claims["email"]),
            name=str(claims["name"]),
            role=str(claims["role"]),
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    key_path = os.getenv("JWT_PUBLIC_KEY_PATH", "/run/secrets/jwt_public.pem")
    try:
        public_key = _read_public_key(key_path)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Authentication is unavailable") from exc
    return decode_access_token(credentials.credentials, public_key)


def require_role(*roles: str) -> Callable[..., CurrentUser]:
    """Create a FastAPI dependency that restricts an endpoint to selected roles."""

    async def check_role(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return check_role
