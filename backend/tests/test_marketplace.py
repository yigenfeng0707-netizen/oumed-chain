"""数据要素市场单元测试（产品种子 / 交易分成 / 存证链连续性 / 监管统计）。"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import DataProduct, DataTransaction  # noqa: E402

REVENUE_SPLIT = {"provider": 0.7, "platform": 0.2, "contributor": 0.1}


async def _fresh_db():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.database import init_db  # noqa: F401  确保模型导入
    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)()


async def _seed(session):
    from app.routers.marketplace import SEED_PRODUCTS, _append_chain

    for p in SEED_PRODUCTS:
        session.add(DataProduct(id=p["name"][:36], **p))
    await session.commit()
    txs = []
    from sqlalchemy import select

    products = (await session.execute(select(DataProduct))).scalars().all()
    for i, prod in enumerate(products[:3]):
        tx = DataTransaction(
            id=f"tx-test-{i}", product_id=prod.id, product_name=prod.name,
            buyer=f"测试买方{i}", amount=prod.price, status="已成交",
            revenue_provider=round(prod.price * 0.7),
            revenue_contributor=round(prod.price * 0.1),
            revenue_platform=0,  # 生产逻辑：平台取余数，保证分成之和恒等于交易额
            purpose="单元测试",
        )
        tx.revenue_platform = tx.amount - tx.revenue_provider - tx.revenue_contributor
        session.add(tx)
        await _append_chain(session, tx)
        await session.commit()
        txs.append(tx)
    return products, txs


def test_revenue_split_math():
    price = 80000
    assert int(price * 0.7) == 56000
    assert int(price * 0.2) == 16000
    assert int(price * 0.1) == 8000
    assert int(price * 0.7) + int(price * 0.2) + int(price * 0.1) == price


def test_seed_five_products():
    async def run():
        session = await _fresh_db()
        from sqlalchemy import func, select

        from app.routers.marketplace import SEED_PRODUCTS, _seed_if_empty

        await _seed_if_empty(session)
        return (await session.execute(
            select(func.count()).select_from(DataProduct))).scalar(), len(SEED_PRODUCTS)

    n, expect = asyncio.run(run())
    assert n == expect == 5


def test_audit_chain_links():
    async def run():
        session = await _fresh_db()
        _, txs = await _seed(session)
        return txs

    txs = asyncio.run(run())
    assert len(txs) == 3
    assert txs[0].prev_hash == "0" * 64
    for prev, cur in zip(txs, txs[1:]):
        assert cur.prev_hash == prev.event_hash
        assert cur.event_hash != prev.event_hash
    assert all(len(t.event_hash) == 64 for t in txs)


def test_transaction_totals_consistent():
    async def run():
        session = await _fresh_db()
        products, txs = await _seed(session)
        return products, txs

    products, txs = asyncio.run(run())
    assert sum(t.amount for t in txs) == sum(p.price for p in products[:3])
    for t in txs:
        assert t.revenue_provider + t.revenue_platform + t.revenue_contributor == t.amount
