"""P0-1.2 等保三级·安全审计留存测试（app/services/audit_retention.py）：

- 超期（>180 天）审计记录先归档库外 JSONL（含 .sha256 完整性校验）再删除
- 未超期记录保留；无过期记录时不产生归档文件
- 调度关闭逻辑（AUDIT_CLEANUP_INTERVAL_HOURS=0 → 不启动任务）

内存 SQLite，不触发 lifespan（与 test_rbac 同构）。
"""

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.models import AccessDenialLog  # noqa: E402
from app.services import audit_retention  # noqa: E402


@pytest_asyncio.fixture
async def session_factory(tmp_path, monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # 归档目录指向临时目录，避免污染真实 audit_archive/
    monkeypatch.setattr(settings, "AUDIT_ARCHIVE_DIR", str(tmp_path / "audit_archive"))
    yield factory
    await engine.dispose()


def _add_log(db, *, days_ago: int, reason: str = "cross_user_access", status_code: int = 403):
    db.add(AccessDenialLog(
        ts=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
        method="GET", path=f"/api/users/user_{days_ago:03d}/profile",
        target_user_id="user_002", status_code=status_code,
        reason=reason, client_ip="203.0.113.9", token_present=True,
    ))


@pytest.mark.asyncio
async def test_expired_archived_and_purged(session_factory):
    """超期记录：先归档（JSONL+sha256）后删除；未超期保留。"""
    async with session_factory() as db:
        _add_log(db, days_ago=200)   # 超期 → 归档删除
        _add_log(db, days_ago=100)   # 未超期 → 保留
        _add_log(db, days_ago=1)     # 未超期 → 保留
        await db.commit()

    async with session_factory() as db:
        info = await audit_retention.archive_and_purge(db, retention_days=180)

    assert info["archived"] == 1 and info["purged"] == 1
    assert info["retention_days"] == 180
    async with session_factory() as db:
        from sqlalchemy import select, func
        remain = (await db.execute(select(func.count()).select_from(AccessDenialLog))).scalar()
        assert remain == 2
        # 归档文件内容
        archive_dir = Path(settings.AUDIT_ARCHIVE_DIR)
        jsonl = next(archive_dir.glob("*.jsonl"))
        text = jsonl.read_text(encoding="utf-8")
        assert "user_200" in text and "cross_user_access" in text
        # sha256 伴随文件校验
        expected = hashlib.sha256(jsonl.read_bytes()).hexdigest()
        assert expected == info["sha256"]
        sha_file = next(archive_dir.glob("*.sha256"))
        assert expected in sha_file.read_text(encoding="ascii")


@pytest.mark.asyncio
async def test_no_expired_no_archive(session_factory):
    """无超期记录：不产生归档文件，purged=0。"""
    async with session_factory() as db:
        _add_log(db, days_ago=10)
        await db.commit()
        info = await audit_retention.archive_and_purge(db, retention_days=180)

    assert info["purged"] == 0 and info["archive_file"] is None
    archive_dir = Path(settings.AUDIT_ARCHIVE_DIR)
    if archive_dir.exists():
        assert not list(archive_dir.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_scheduler_disabled_when_interval_zero(session_factory, monkeypatch):
    """清理周期为 0 → 不启动调度任务（仅手动归档）。"""
    monkeypatch.setattr(settings, "AUDIT_CLEANUP_INTERVAL_HOURS", 0)
    assert audit_retention.start_retention_task() is None


@pytest.mark.asyncio
async def test_scheduler_loop_recovers_from_error(session_factory, monkeypatch):
    """循环内异常不杀循环（归档失败仅记日志，下周期重试）。"""
    monkeypatch.setattr(settings, "AUDIT_CLEANUP_INTERVAL_HOURS", 1)
    calls = {"n": 0}

    async def boom():
        calls["n"] += 1
        raise RuntimeError("模拟库故障")

    monkeypatch.setattr(audit_retention, "scheduled_retention_once", boom)
    task = audit_retention.start_retention_task()
    assert task is not None
    await asyncio.sleep(0.2)
    # 异常被循环吞掉：任务未结束（若异常未捕获，task 早已 finished with exception）
    assert not task.done()
    assert calls["n"] == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
