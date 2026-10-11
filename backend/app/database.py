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
        # SQLite 轻量迁移：给已有表补新增列
        # （create_all 只建新表不改旧表；重复执行时忽略 DuplicateColumn）
        for ddl in (
            "ALTER TABLE users ADD COLUMN email VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN role VARCHAR(30) NOT NULL DEFAULT 'member'",
            # P1.3 节点存活可见性：最后活跃时间（可空，历史节点未上报前为 NULL）
            "ALTER TABLE federated_nodes ADD COLUMN last_seen_at DATETIME",
            # P2.3 多租户：医院级租户归属（可空，历史数据 NULL=平台直管）
            "ALTER TABLE users ADD COLUMN hospital_id INTEGER",
            "ALTER TABLE federated_nodes ADD COLUMN hospital_id INTEGER",
            "ALTER TABLE distributed_federation_jobs ADD COLUMN hospital_id INTEGER",
        ):
            with suppress(Exception):  # 列已存在
                await conn.execute(text(ddl))
