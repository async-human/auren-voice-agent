"""Map a verified provider identity onto a stable internal user."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import User, utcnow


async def resolve_user(
    session: AsyncSession,
    external_id: str,
    email: str | None = None,
    display_name: str | None = None,
) -> User:
    user = await _find(session, external_id)

    if user is None:
        user = User(
            external_id=external_id,
            email=email,
            display_name=display_name if display_name and display_name != "Local developer" else None,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            # Two sessions can race on a user's first sign-in.
            await session.rollback()
            user = await _find(session, external_id)
            if user is None:
                raise
        else:
            await session.refresh(user)
            return user

    if email and user.email != email:
        user.email = email
    # Never blank out a remembered name just because the provider omitted one.
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    user.last_seen_at = utcnow()
    await session.commit()
    await session.refresh(user)
    return user


async def _find(session: AsyncSession, external_id: str) -> User | None:
    result = await session.execute(select(User).where(User.external_id == external_id))
    return result.scalar_one_or_none()
