from contextlib import suppress

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with async_session() as session:
        yield session


async def init_db():
    from sqlalchemy import text

    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 轻量迁移：给已有 users 表补 email/password_hash 列
        # （create_all 只建新表不改旧表；重复执行时忽略 DuplicateColumn）
        for ddl in (
            "ALTER TABLE users ADD COLUMN email VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)",
        ):
            with suppress(Exception):  # 列已存在
                await conn.execute(text(ddl))
