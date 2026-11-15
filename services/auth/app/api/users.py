"""Admin-only user directory and role/status management."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_redis, invalidate_profile
from app.db import get_session
from app.models import RefreshToken, User, UserRole
from app.schemas import Profile, UserList, UserPatch
from helpdesk_common import CurrentUser, require_role

router = APIRouter(prefix="/api/v1/users", tags=["users"])
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[CurrentUser, Depends(require_role("admin"))]
RedisClient = Annotated[Redis, Depends(get_redis)]


@router.get("", response_model=UserList)
async def list_users(
    session: Session,
    _admin: Admin,
    role: UserRole | None = None,
    page: Annotated[int, Query(ge=1, le=2**31 - 1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> UserList:
    filters = [User.role == role] if role is not None else []
    total = await session.scalar(select(func.count()).select_from(User).where(*filters))
    rows = await session.scalars(
        select(User)
        .where(*filters)
        .order_by(User.created_at.desc(), User.id)
        .offset((page - 1) * size)
        .limit(size)
    )
    return UserList(
        items=[Profile.from_user(user) for user in rows],
        total=total or 0,
        page=page,
        size=size,
    )


@router.get("/{user_id}", response_model=Profile)
async def get_user(user_id: UUID, session: Session, _admin: Admin) -> Profile:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return Profile.from_user(user)


@router.patch("/{user_id}", response_model=Profile)
async def patch_user(
    user_id: UUID,
    payload: UserPatch,
    session: Session,
    _admin: Admin,
    redis: RedisClient,
) -> Profile:
    fields = payload.model_fields_set
    if not fields:
        raise HTTPException(status_code=422, detail="At least one field must be provided")
    if any(getattr(payload, name) is None for name in fields):
        raise HTTPException(status_code=422, detail="Updated fields cannot be null")

    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    for name in fields:
        setattr(user, name, getattr(payload, name))
    if "is_active" in fields and not payload.is_active:
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now())
        )
    await session.commit()
    await session.refresh(user)
    await invalidate_profile(redis, user_id)
    return Profile.from_user(user)
