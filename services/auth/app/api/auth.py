"""Registration, login, token rotation, logout, and self profile routes."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import (
    cache_profile,
    clear_login_failures,
    get_cached_profile,
    get_redis,
    login_locked,
    record_login_failure,
)
from app.db import get_session
from app.models import RefreshToken, User, UserRole
from app.schemas import (
    LoginRequest,
    Profile,
    RefreshRequest,
    RegisterRequest,
    RegistrationResponse,
    TokenResponse,
)
from app.security import decode_refresh_token, hash_password, issue_token_pair, verify_password
from helpdesk_common import CurrentUser, get_current_user

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
Session = Annotated[AsyncSession, Depends(get_session)]
Current = Annotated[CurrentUser, Depends(get_current_user)]
RedisClient = Annotated[Redis, Depends(get_redis)]


def _invalid_credentials() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid email or password")


async def _store_refresh(
    session: AsyncSession, user: User, pair, user_agent: str | None
) -> None:
    session.add(
        RefreshToken(
            jti=pair.refresh_jti,
            user_id=user.id,
            expires_at=pair.refresh_expires_at,
            user_agent=user_agent[:500] if user_agent else None,
        )
    )


def _token_response(pair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: Session) -> RegistrationResponse:
    user = User(
        id=uuid4(),
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.CLIENT,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Email is already registered") from exc
    await session.refresh(user)
    return RegistrationResponse(
        id=user.id, email=user.email, full_name=user.full_name, role=user.role
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: Session,
    redis: RedisClient,
) -> TokenResponse:
    email = str(payload.email).lower()
    retry_after = await login_locked(redis, email)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )

    user = await session.scalar(select(User).where(User.email == email))
    # Verify a fixed dummy hash when the account is absent to reduce timing leaks.
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    valid_password = verify_password(password_hash, payload.password)
    if user is None or not user.is_active or not valid_password:
        await record_login_failure(redis, email)
        raise _invalid_credentials()

    await clear_login_failures(redis, email)
    try:
        pair = issue_token_pair(user)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Authentication is unavailable") from exc
    await _store_refresh(session, user, pair, request.headers.get("user-agent"))
    await session.commit()
    return _token_response(pair)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    session: Session,
) -> TokenResponse:
    try:
        claims = decode_refresh_token(payload.refresh_token)
        user_id = UUID(claims["sub"])
        refresh_jti = UUID(claims["jti"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_credentials() from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Authentication is unavailable") from exc

    now = datetime.now(UTC)
    token_reuse = False
    pair = None
    async with session.begin():
        user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
        token = await session.scalar(
            select(RefreshToken)
            .where(RefreshToken.jti == refresh_jti, RefreshToken.user_id == user_id)
            .with_for_update()
        )
        if user is not None and token is not None and token.revoked_at is not None:
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            token_reuse = True
        elif (
            user is None
            or not user.is_active
            or token is None
            or token.expires_at <= now
        ):
            token = None
        else:
            token.revoked_at = now
            try:
                pair = issue_token_pair(user)
            except OSError as exc:
                raise HTTPException(
                    status_code=503, detail="Authentication is unavailable"
                ) from exc
            await _store_refresh(session, user, pair, request.headers.get("user-agent"))

    if token_reuse or pair is None:
        raise _invalid_credentials()
    return _token_response(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    session: Session,
    current: Current,
) -> Response:
    try:
        claims = decode_refresh_token(payload.refresh_token)
        if UUID(claims["sub"]) != UUID(current.id):
            raise _invalid_credentials()
        refresh_jti = UUID(claims["jti"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise _invalid_credentials() from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Authentication is unavailable") from exc

    token = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.jti == refresh_jti,
            RefreshToken.user_id == UUID(current.id),
            RefreshToken.revoked_at.is_(None),
        )
    )
    if token is not None:
        token.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=Profile)
async def me(current: Current, session: Session, redis: RedisClient) -> Profile:
    user_id = UUID(current.id)
    cached = await get_cached_profile(redis, user_id)
    if cached is not None:
        return cached
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    profile = Profile.from_user(user)
    await cache_profile(redis, profile)
    return profile


_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-only")
