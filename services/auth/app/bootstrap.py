"""One-time admin account bootstrap."""

from uuid import uuid4

from sqlalchemy import select

from app.config import settings
from app.db import session_factory
from app.models import User, UserRole
from app.security import hash_password


async def seed_admin() -> None:
    async with session_factory() as session:
        async with session.begin():
            admin = await session.scalar(
                select(User.id).where(User.role == UserRole.ADMIN).limit(1)
            )
            if admin is not None:
                return
            existing = await session.scalar(
                select(User.id).where(User.email == str(settings.admin_email).lower())
            )
            if existing is not None:
                raise RuntimeError("ADMIN_EMAIL already belongs to a non-admin user")
            password = settings.admin_password.get_secret_value()
            if len(password) < 8:
                raise RuntimeError("ADMIN_PASSWORD must be set to at least 8 characters")
            session.add(
                User(
                    id=uuid4(),
                    email=str(settings.admin_email).lower(),
                    password_hash=hash_password(password),
                    full_name="HelpDesk Administrator",
                    role=UserRole.ADMIN,
                )
            )
