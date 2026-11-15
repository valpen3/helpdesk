"""Password hashing and RS256 token issuance/verification."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization

from app.config import settings
from app.models import User

ISSUER = "helpdesk-auth"
_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def validate_key_pair() -> None:
    private_key = serialization.load_pem_private_key(
        Path(settings.jwt_private_key_path).read_bytes(), password=None
    )
    public_key = serialization.load_pem_public_key(Path(settings.jwt_public_key_path).read_bytes())
    if private_key.public_key().public_numbers() != public_key.public_numbers():
        raise RuntimeError("JWT private and public keys do not match")


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_jti: UUID
    refresh_expires_at: datetime
    expires_in: int


def issue_token_pair(user: User) -> TokenPair:
    now = datetime.now(UTC)
    access_ttl = timedelta(minutes=settings.access_token_ttl_min)
    refresh_ttl = timedelta(days=settings.refresh_token_ttl_days)
    refresh_jti = uuid4()
    common = {
        "sub": str(user.id),
        "email": user.email,
        "name": user.full_name,
        "role": user.role.value,
        "iat": now,
        "iss": ISSUER,
    }
    private_key = Path(settings.jwt_private_key_path).read_bytes()
    access = jwt.encode(
        {**common, "jti": str(uuid4()), "exp": now + access_ttl, "type": "access"},
        private_key,
        algorithm="RS256",
    )
    refresh = jwt.encode(
        {
            **common,
            "jti": str(refresh_jti),
            "exp": now + refresh_ttl,
            "type": "refresh",
        },
        private_key,
        algorithm="RS256",
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        refresh_jti=refresh_jti,
        refresh_expires_at=now + refresh_ttl,
        expires_in=int(access_ttl.total_seconds()),
    )


def decode_refresh_token(token: str) -> dict:
    public_key = Path(settings.jwt_public_key_path).read_bytes()
    claims = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=ISSUER,
        options={"require": ["sub", "jti", "iat", "exp", "iss", "type"]},
    )
    if claims.get("type") != "refresh":
        raise jwt.InvalidTokenError("not a refresh token")
    UUID(claims["sub"])
    UUID(claims["jti"])
    return claims
