"""瓯医数链 · 联邦学习协作路由

数据要素协作底座的真实实现：
- 医院数据全景（联邦统计口径，无个体记录）
- 发起联邦训练任务（支持差分隐私），结果入库并接入审计存证链
- 标准基准实验（本地 vs 联邦 vs DP 分档 vs 集中上界）
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import FederationJob
from app.services.federated import get_benchmark, get_overview, run_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/federation", tags=["联邦学习协作"])


class JobRequest(BaseModel):
    task: str = Field(default="hf_readmission", description="任务类型")
    rounds: int = Field(default=12, ge=1, le=50, description="联邦轮次")
    local_epochs: int = Field(default=3, ge=1, le=20, description="每轮本地epoch")
    dp_sigma: float = Field(default=0.0, ge=0.0, le=1.0, description="差分隐私噪声强度σ（0=关闭）")
    clip_norm: float | None = Field(default=None, gt=0, description="DP裁剪阈值（默认按σ自动标定）")


def _event_digest(job: FederationJob) -> str:
    payload = json.dumps({
        "id": job.id, "task": job.task, "rounds": job.rounds,
        "local_epochs": job.local_epochs, "dp_sigma": job.dp_sigma,
        "clip_norm": job.clip_norm, "status": job.status,
        "final_auc": json.loads(job.result).get("final_auc") if job.result else None,
        "created_at": job.created_at.isoformat() if job.created_at else "",
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _append_chain(db: AsyncSession, job: FederationJob):
    """把任务结果接入审计存证链：event_hash = sha256(prev_hash + 本任务摘要)。"""
    last = (await db.execute(
        select(FederationJob)
        .where(FederationJob.event_hash.isnot(None))
        .order_by(FederationJob.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    job.prev_hash = last.event_hash if last else "0" * 64
    job.event_hash = hashlib.sha256(
        (job.prev_hash + _event_digest(job)).encode("utf-8")).hexdigest()


def _job_to_dict(job: FederationJob, include_result: bool = True) -> dict:
    d = {
        "id": job.id,
        "task": job.task,
        "rounds": job.rounds,
        "local_epochs": job.local_epochs,
        "dp_sigma": job.dp_sigma,
        "clip_norm": job.clip_norm,
        "status": job.status,
        "duration_ms": job.duration_ms,
        "prev_hash": job.prev_hash,
        "event_hash": job.event_hash,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
    if include_result and job.result:
        d["result"] = json.loads(job.result)
    return d


@router.get("/overview")
def federation_overview():
    """三家医院数据全景（联邦统计口径）。"""
    return get_overview()


@router.post("/jobs")
async def create_job(req: JobRequest, db: AsyncSession = Depends(get_db)):
    """发起联邦训练任务（同步执行，秒级返回）。"""
    job = FederationJob(
        id=str(uuid.uuid4()),
        task=req.task,
        rounds=req.rounds,
        local_epochs=req.local_epochs,
        dp_sigma=req.dp_sigma,
        clip_norm=req.clip_norm if req.clip_norm is not None else (0.2 if req.dp_sigma > 0 else 1.0),
        status="running",
    )
    db.add(job)
    await db.commit()

    start = time.monotonic()
    try:
        # 同步计算密集段；FastAPI 对 async 路由默认在事件循环执行，
        # 此处显式放入线程池，避免阻塞其他请求
        import anyio
        result = await anyio.to_thread.run_sync(
            lambda: run_job(rounds=req.rounds, local_epochs=req.local_epochs,
                            dp_sigma=req.dp_sigma, clip_norm=job.clip_norm),
        )
        job.result = json.dumps(result, ensure_ascii=False)
        job.status = "done"
    except Exception as e:  # noqa: BLE001
        logger.exception("联邦任务失败 %s", job.id)
        job.status = "failed"
        job.result = json.dumps({"error": str(e)}, ensure_ascii=False)
    job.duration_ms = int((time.monotonic() - start) * 1000)
    await _append_chain(db, job)
    await db.commit()
    return _job_to_dict(job)


@router.get("/jobs")
async def list_jobs(limit: int = 20, db: AsyncSession = Depends(get_db)):
    """最近联邦任务列表（含存证哈希）。"""
    jobs = (await db.execute(
        select(FederationJob).order_by(FederationJob.created_at.desc()).limit(limit)
    )).scalars().all()
    return [_job_to_dict(j, include_result=False) for j in jobs]


@router.get("/benchmark")
def benchmark(force: bool = False):
    """标准基准实验（首次调用约1分钟，之后走缓存）。同步路由自动在线程池执行。"""
    return get_benchmark(force=force)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await db.get(FederationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return _job_to_dict(job)
