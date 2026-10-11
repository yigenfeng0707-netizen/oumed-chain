"""租户计费计量（P2.3 多租户）：按 (日期, 医院, 指标) upsert 累加 + 报表聚合。

计量指标：
- federated_jobs：医院发起的分布式联邦任务数（federation_nodes.create_job）
- node_updates：医院节点的统计/参数上行次数（moments/update 落库成功）

报表口径：与 tenant_usage_daily 表逐条一致（验收"计量报表准确"的唯一事实源）。
commit 时机由调用方决定（计量写入与业务写入同事务，保证一致性）。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TenantUsageDaily

# 指标枚举（与各写入点注释同口径）
METRICS = ("federated_jobs", "node_updates")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def record_usage(db: AsyncSession, hospital_id: int | None, metric: str,
                       value: int = 1, usage_date: str | None = None) -> None:
    """计量累加（upsert：同日同院同指标只一行）。hospital_id 为空直接跳过。

    与业务写入同事务：本函数只 add，不 commit（调用方统一提交）。
    """
    if hospital_id is None or value == 0:
        return
    day = usage_date or _today()
    row = (await db.execute(
        select(TenantUsageDaily).where(
            TenantUsageDaily.usage_date == day,
            TenantUsageDaily.hospital_id == hospital_id,
            TenantUsageDaily.metric == metric,
        )
    )).scalar_one_or_none()
    if row is None:
        db.add(TenantUsageDaily(usage_date=day, hospital_id=hospital_id,
                                metric=metric, value=value))
    else:
        row.value += value


async def usage_report(db: AsyncSession, hospital_id: int | None = None,
                       date_from: str | None = None, date_to: str | None = None) -> dict:
    """计量报表：rows 明细 + totals 指标合计。

    hospital_id=None → 全部租户（operator/超管口径）；指定 → 单租户。
    """
    stmt = select(TenantUsageDaily).order_by(
        TenantUsageDaily.usage_date.desc(), TenantUsageDaily.hospital_id,
        TenantUsageDaily.metric,
    )
    if hospital_id is not None:
        stmt = stmt.where(TenantUsageDaily.hospital_id == hospital_id)
    if date_from:
        stmt = stmt.where(TenantUsageDaily.usage_date >= date_from)
    if date_to:
        stmt = stmt.where(TenantUsageDaily.usage_date <= date_to)
    rows = (await db.execute(stmt)).scalars().all()

    totals: dict[str, int] = {}
    for r in rows:
        totals[r.metric] = totals.get(r.metric, 0) + r.value
    return {
        "rows": [{
            "usage_date": r.usage_date,
            "hospital_id": r.hospital_id,
            "metric": r.metric,
            "value": r.value,
        } for r in rows],
        "totals": totals,
    }


async def usage_summary(db: AsyncSession) -> list[dict]:
    """按租户汇总（管理端医院列表附用量概览用，全部日期合计）。"""
    stmt = select(
        TenantUsageDaily.hospital_id,
        TenantUsageDaily.metric,
        func.sum(TenantUsageDaily.value),
    ).group_by(TenantUsageDaily.hospital_id, TenantUsageDaily.metric)
    out: dict[int, dict[str, int]] = {}
    for hid, metric, total in await db.execute(stmt):
        out.setdefault(hid, {})[metric] = int(total or 0)
    return [{"hospital_id": hid, "totals": m} for hid, m in sorted(out.items())]
