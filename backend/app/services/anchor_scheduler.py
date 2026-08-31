"""存证链定时锚定调度器（后台任务，零新依赖）。

周期把链尖摘要送公共可信时间戳机构（RFC 3161），让平台自身也无法
伪造历史；TSA 不可达时降级离线留痕（由 chain_anchor 内部保证）。
调度循环由 main.py lifespan 拉起，失败只记日志，绝不拖垮主服务。
"""

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def scheduled_anchor_once() -> dict | None:
    """执行一次锚定（自建会话，独立于请求上下文）。返回锚定摘要字典。"""
    from app.database import async_session
    from app.services import chain_anchor

    async with async_session() as db:
        anchor = await chain_anchor.create_anchor(
            db, tsa_url=settings.CHAIN_ANCHOR_TSA_URL or None
        )
        return chain_anchor.anchor_to_dict(anchor)


async def anchor_loop(interval_hours: float) -> None:
    """锚定循环：启动后先立即锚一次，随后按周期重复（取消即退出）。"""
    interval = max(interval_hours, 0.01) * 3600
    while True:
        try:
            info = await scheduled_anchor_once()
            logger.info("定时锚定完成：status=%s tip=%s",
                        (info or {}).get("status"), (info or {}).get("tip_hash", "")[:16])
        except Exception as exc:  # 任何异常都不能杀死调度循环
            logger.warning("定时锚定失败（下周期重试）：%s", exc)
        await asyncio.sleep(interval)


def start_anchor_task() -> asyncio.Task | None:
    """按配置启动调度任务；间隔为 0 时返回 None（仅手动锚定）。"""
    if settings.CHAIN_ANCHOR_INTERVAL_HOURS <= 0:
        return None
    return asyncio.create_task(
        anchor_loop(settings.CHAIN_ANCHOR_INTERVAL_HOURS),
        name="chain-anchor-scheduler",
    )
