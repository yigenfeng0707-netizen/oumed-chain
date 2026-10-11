"""安全审计留存策略（P0-1.2 等保三级：安全审计留存 ≥6 个月）。

等保 2.0 三级「安全审计」要求审计记录留存不少于 6 个月。策略：
- 库内只保留最近 AUDIT_RETENTION_DAYS（默认 180 天）的审计记录；
- 超期记录在删除前**先归档**为库外 JSONL 文件（append-only 目录 audit_archive/），
  并生成伴随 .sha256 完整性校验文件——归档后可长期保存/离线存储，满足"留存≥6个月"
  且不因库清理丢失历史；
- 调度复用 anchor_scheduler 同款模式：main.py lifespan 拉起后台循环，
  周期可配（AUDIT_CLEANUP_INTERVAL_HOURS，0 = 关闭仅手动执行），异常不杀循环。

当前覆盖的审计表：access_denial_logs（越权访问审计）。新增审计表时在该表清单中追加。
"""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AccessDenialLog

logger = logging.getLogger(__name__)

# 归档目录（相对后端运行目录；可通过 env AUDIT_ARCHIVE_DIR 覆盖）
DEFAULT_ARCHIVE_DIR = "./audit_archive"


def _archive_dir() -> Path:
    return Path(settings.AUDIT_ARCHIVE_DIR or DEFAULT_ARCHIVE_DIR)


def _log_to_dict(log: AccessDenialLog) -> dict:
    return {
        "id": log.id,
        "ts": log.ts.isoformat() if log.ts else None,
        "method": log.method,
        "path": log.path,
        "target_user_id": log.target_user_id,
        "status_code": log.status_code,
        "reason": log.reason,
        "client_ip": log.client_ip,
        "token_present": bool(log.token_present),
    }


def _write_archive(records: list[dict]) -> tuple[Path, str]:
    """把超期审计记录写入 JSONL 归档文件，返回 (文件路径, sha256)。

    文件名带时间戳与行数，append-only：绝不覆盖既有归档文件。
    """
    archive_dir = _archive_dir()
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    path = archive_dir / f"audit_access_denials_{stamp}_{len(records)}rows.jsonl"
    payload = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in records)
    data = (payload + "\n").encode("utf-8")
    # 防极小概率撞名：同秒同行数重跑时追加序号
    n = 1
    while path.exists():
        path = path.with_name(path.name.replace(".jsonl", f"_{n}.jsonl"))
        n += 1
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    path.with_suffix(".jsonl.sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return path, digest


async def archive_and_purge(
    db: AsyncSession,
    retention_days: int | None = None,
) -> dict:
    """归档并清理超期审计记录（自建会话可传 None 外部调用）。

    返回 {"retention_days", "archived", "purged", "archive_file", "sha256"}。
    """
    days = retention_days if retention_days is not None else settings.AUDIT_RETENTION_DAYS
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    result = await db.execute(
        select(AccessDenialLog).where(AccessDenialLog.ts < cutoff).order_by(AccessDenialLog.ts)
    )
    expired = list(result.scalars().all())
    if not expired:
        return {"retention_days": days, "archived": 0, "purged": 0,
                "archive_file": None, "sha256": None}

    path, digest = _write_archive([_log_to_dict(r) for r in expired])
    await db.execute(
        delete(AccessDenialLog).where(AccessDenialLog.id.in_([r.id for r in expired]))
    )
    await db.commit()
    logger.info("审计归档完成：%d 条超期记录 → %s（sha256=%s…）",
                len(expired), path.name, digest[:16])
    return {"retention_days": days, "archived": len(expired), "purged": len(expired),
            "archive_file": path.name, "sha256": digest}


async def scheduled_retention_once() -> dict:
    """执行一次归档清理（自建会话，独立于请求上下文）。"""
    from app.database import async_session

    async with async_session() as db:
        return await archive_and_purge(db)


async def retention_loop(interval_hours: float) -> None:
    """留存循环：启动后先执行一次，随后按周期重复（取消即退出）。"""
    interval = max(interval_hours, 0.01) * 3600
    while True:
        try:
            info = await scheduled_retention_once()
            if info.get("purged"):
                logger.info("定时审计留存完成：%s", info)
        except Exception as exc:  # 任何异常都不能杀死调度循环
            logger.warning("定时审计留存失败（下周期重试）：%s", exc)
        await asyncio.sleep(interval)


def start_retention_task() -> asyncio.Task | None:
    """按配置启动留存调度任务；周期为 0 时返回 None（仅手动归档）。"""
    if settings.AUDIT_CLEANUP_INTERVAL_HOURS <= 0:
        return None
    return asyncio.create_task(
        retention_loop(settings.AUDIT_CLEANUP_INTERVAL_HOURS),
        name="audit-retention-scheduler",
    )
