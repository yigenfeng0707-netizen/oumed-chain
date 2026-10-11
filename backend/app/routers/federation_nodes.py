"""瓯医数链 · 联邦节点协作路由（P1.1 医院侧节点真实部署的平台侧 Coordinator）。

与进程内仿真（federation.py）的区别：真实部署形态，医院内网节点 Agent 通过
HTTP 协议逐轮握手，平台只聚合加密参数，原始数据零传输。

端点分两面：
- 管理面（RBAC require_roles，双模）：节点注册/列表、任务创建/列表/详情；
- 节点面（X-Node-Id + X-Node-Token 机器凭证，恒强制——M2M 无用户摩擦，
  不适用 DEMO_MODE 放行）：任务发现、assignment 拉取、统计/参数加密上行。

协议流程：collecting（收集联邦统计）→ running（逐轮：拉全局模型→本地训练
→加密上传→集齐聚合推进）→ done（加权 AUC + 任务级存证链）。
"""

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import numpy as np

from app.config import settings
from app.database import get_db
from app.deps import require_roles
from app.models import DistributedJob, DistributedUpdate, FederatedNode, Hospital
from app.services.federation_coordinator import (aggregate_coef, aggregate_moments,
                                                  open_envelope, weighted_auc)
from app.services.privacy_accountant import privacy_report
from app.services.tenant_usage import record_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/federation/nodes", tags=["联邦节点协作"])


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class NodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="医院/节点名称")
    # P2.3 多租户：operator 可指定归属租户；hospital_admin 忽略该字段（强制本院）
    hospital_id: int | None = Field(default=None, description="归属医院租户 id（operator 用）")


class DistJobCreate(BaseModel):
    node_ids: list[str] = Field(min_length=2, description="参与节点（≥2 家机构）")
    task: str = Field(default="hf_readmission", description="任务类型")
    rounds: int = Field(default=12, ge=1, le=50)
    local_epochs: int = Field(default=3, ge=1, le=20)
    dp_sigma: float = Field(default=0.0, ge=0.0, le=1.0, description="差分隐私噪声σ")
    clip_norm: float | None = Field(default=None, gt=0)


class EnvelopeBody(BaseModel):
    payload: str = Field(min_length=1, description="AES-GCM 加密信封（enc2:key_id:nonce:ct，旧 enc1 过渡兼容）或联调明文 JSON")


# ---------------------------------------------------------------------------
# 节点鉴权（机器凭证，恒强制）
# ---------------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _require_node(
    db: AsyncSession,
    x_node_id: str | None,
    x_node_token: str | None,
) -> FederatedNode:
    """节点机器凭证校验：sha256 恒定时间比对 + active 状态。

    P1.3 存活可见性：认证通过即刷新 last_seen_at（立即提交，节点面四个端点
    全部生效）；管理端据此判断节点掉线（如拔线 30 分钟演练中 last_seen 停更）。
    """
    if not x_node_id or not x_node_token:
        raise HTTPException(status_code=401, detail="缺少节点凭证（X-Node-Id / X-Node-Token）")
    node = await db.get(FederatedNode, x_node_id)
    if (node is None or node.status != "active"
            or not hmac.compare_digest(node.token_hash, _token_hash(x_node_token))):
        raise HTTPException(status_code=401, detail="节点凭证无效或已停用")
    node.last_seen_at = datetime.now(UTC)
    await db.commit()
    return node


def _require_member(job: DistributedJob, node: FederatedNode):
    if node.id not in json.loads(job.expected_nodes):
        raise HTTPException(status_code=403, detail="节点未被指派到该任务")


async def _get_job(db: AsyncSession, job_id: str) -> DistributedJob:
    job = await db.get(DistributedJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return job


def _open_payload(payload: str) -> dict:
    try:
        return open_envelope(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------------------------------------------------------
# P2.3 多租户：管理面租户作用域
# ---------------------------------------------------------------------------

def _tenant_scope(actor: dict) -> int | None:
    """管理面租户作用域：hospital_admin → 本院 id（token 未绑定 → 403）；
    operator / 超管 → None（全局视图）。

    hospital_id 写在 HMAC 签名 token payload 内，不可伪造；
    用户被绑定租户后重新登录生效（无状态 token 权衡）。
    """
    if actor["role"] == "hospital_admin":
        hid = actor.get("hospital_id")
        if hid is None:
            raise HTTPException(
                status_code=403,
                detail="医院管理员账号未绑定租户，请联系平台分配归属医院后重新登录")
        return hid
    return None


async def _assert_owned_job(job: DistributedJob, scope: int | None):
    """写/读操作资源归属校验：租户管理员只能操作本院发起的任务。

    非本院任务按 404 处理（不泄露跨租户资源存在性）。
    """
    if scope is not None and job.hospital_id != scope:
        raise HTTPException(status_code=404, detail=f"任务 {job.id} 不存在")


def _check_update_sanity(job: DistributedJob, coef: list[float]):
    """P1.3 恶意参数方向检测：上行参数与当前全局模型 cosine 相似度过低 → 422。

    合法本地更新（含 DP 噪声）方向与全局模型高度一致（接近 1）；翻转/投毒参数
    方向相反（≈-1），随机垃圾接近 0。全局模型尚为零向量（首轮初始）时方向无意义，
    跳过检测；阈值 FEDERATION_COS_THRESHOLD（默认 0.0，负数关闭）。拒绝发生在
    落库之前——恶意参数不进入 DistributedUpdate 表、不参与聚合。
    """
    threshold = settings.FEDERATION_COS_THRESHOLD
    if threshold < 0 or not job.global_coef:
        return
    global_vec = np.asarray(json.loads(job.global_coef), dtype=float)
    norm = float(np.linalg.norm(global_vec))
    if norm < 1e-12:  # 首轮初始零模型：方向无意义
        return
    sim = float(np.dot(global_vec, np.asarray(coef, dtype=float))
                / (norm * max(float(np.linalg.norm(coef)), 1e-12)))
    if sim < threshold:
        logger.warning("恶意参数检测拦截: job=%s coef_cos=%.4f < %.2f（参数未落库）",
                       job.id, sim, threshold)
        raise HTTPException(
            status_code=422,
            detail=f"参数方向校验失败：与全局模型 cosine={sim:.4f} 低于阈值 {threshold}，疑似篡改/投毒，已拒绝")


# ---------------------------------------------------------------------------
# 存证链（任务级，与 federation_jobs 同款 sha256 串联）
# ---------------------------------------------------------------------------

def _event_digest(job: DistributedJob) -> str:
    payload = json.dumps({
        "id": job.id, "task": job.task, "rounds": job.rounds,
        "local_epochs": job.local_epochs, "dp_sigma": job.dp_sigma,
        "clip_norm": job.clip_norm, "status": job.status,
        "nodes": json.loads(job.expected_nodes),
        "final_auc": json.loads(job.result).get("final_auc") if job.result else None,
        "created_at": job.created_at.isoformat() if job.created_at else "",
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _append_chain(db: AsyncSession, job: DistributedJob):
    last = (await db.execute(
        select(DistributedJob)
        .where(DistributedJob.event_hash.isnot(None))
        .order_by(DistributedJob.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    job.prev_hash = last.event_hash if last else "0" * 64
    job.event_hash = hashlib.sha256(
        (job.prev_hash + _event_digest(job)).encode("utf-8")).hexdigest()


def _job_to_dict(job: DistributedJob, include_result: bool = True) -> dict:
    d = {
        "id": job.id, "task": job.task, "rounds": job.rounds,
        "local_epochs": job.local_epochs, "dp_sigma": job.dp_sigma,
        "clip_norm": job.clip_norm, "status": job.status,
        "round": job.current_round, "hospital_id": job.hospital_id,
        "nodes": json.loads(job.expected_nodes),
        "prev_hash": job.prev_hash, "event_hash": job.event_hash,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
    if include_result and job.result:
        d["result"] = json.loads(job.result)
    return d


# ---------------------------------------------------------------------------
# 管理面：节点注册 / 任务创建（双模 RBAC）
# ---------------------------------------------------------------------------

@router.post("")
async def register_node(
    req: NodeCreate,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """注册医院侧节点：node_token 仅本次明文返回，平台只存 sha256 哈希。

    P2.3 多租户：hospital_admin 注册的节点强制归属本院；operator 可指定
    hospital_id（须为已存在租户），不指定为平台直管节点（hospital_id=None）。
    """
    scope = _tenant_scope(actor)
    if scope is not None:
        hid = scope
    elif req.hospital_id is not None:
        hid = req.hospital_id
        if await db.get(Hospital, hid) is None:
            raise HTTPException(status_code=400, detail=f"医院租户 {hid} 不存在")
    else:
        hid = None
    node = FederatedNode(id=str(uuid.uuid4()), name=req.name, hospital_id=hid)
    token = secrets.token_urlsafe(32)
    node.token_hash = _token_hash(token)
    db.add(node)
    await db.commit()
    logger.info("联邦节点注册: %s (%s, 租户=%s)", req.name, node.id, hid)
    return {"node_id": node.id, "name": node.name, "hospital_id": node.hospital_id,
            "node_token": token,
            "note": "节点凭证仅此一次返回，请立即配置到节点 Agent 环境变量 NODE_TOKEN"}


@router.get("")
async def list_nodes(
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """节点列表（不含任何凭证信息；含最后活跃时间用于掉线发现）。

    P2.3 多租户：hospital_admin 仅见本院节点；operator/超管全局。
    """
    stmt = select(FederatedNode).order_by(FederatedNode.created_at)
    scope = _tenant_scope(actor)
    if scope is not None:
        stmt = stmt.where(FederatedNode.hospital_id == scope)
    nodes = (await db.execute(stmt)).scalars().all()
    return [{"id": n.id, "name": n.name, "hospital_id": n.hospital_id,
             "status": n.status,
             "last_seen_at": n.last_seen_at.isoformat() if n.last_seen_at else None,
             "created_at": n.created_at.isoformat() if n.created_at else None} for n in nodes]


@router.post("/jobs")
async def create_job(
    req: DistJobCreate,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """创建分布式联邦任务：指派节点进入 collecting 阶段（等待联邦统计）。

    P2.3 多租户：hospital_admin 发起的任务归属本院（发起租户），且只能指派
    本院节点（跨租户指派 → 403）；operator 可混编多院节点（联邦本意）。
    医院发起即计入计费计量（federated_jobs）。
    """
    scope = _tenant_scope(actor)
    ids = list(dict.fromkeys(req.node_ids))  # 去重保序
    nodes = []
    for nid in ids:
        node = await db.get(FederatedNode, nid)
        if node is None or node.status != "active":
            raise HTTPException(status_code=400, detail=f"节点 {nid} 不存在或已停用")
        if scope is not None and node.hospital_id != scope:
            raise HTTPException(status_code=403, detail=f"节点 {nid} 不属于本院，无法指派")
        nodes.append(node)
    job = DistributedJob(
        id=str(uuid.uuid4()), task=req.task, rounds=req.rounds,
        local_epochs=req.local_epochs, dp_sigma=req.dp_sigma,
        clip_norm=req.clip_norm if req.clip_norm is not None else (0.2 if req.dp_sigma > 0 else 1.0),
        status="collecting", current_round=0, hospital_id=scope,
        expected_nodes=json.dumps(ids, ensure_ascii=False),
    )
    db.add(job)
    await record_usage(db, scope, "federated_jobs")  # 计费计量：任务发起（同事务）
    await db.commit()
    return _job_to_dict(job)


@router.get("/jobs")
async def list_jobs(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """任务列表：hospital_admin 仅见本院发起任务；operator/超管全局。"""
    stmt = select(DistributedJob).order_by(DistributedJob.created_at.desc()).limit(limit)
    scope = _tenant_scope(actor)
    if scope is not None:
        stmt = stmt.where(DistributedJob.hospital_id == scope)
    jobs = (await db.execute(stmt)).scalars().all()
    return [_job_to_dict(j, include_result=False) for j in jobs]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator", "regulator")),
):
    """任务详情（含结果、存证哈希与各节点提交进度；监管角色可查）。

    P1.3 progress：每个被指派节点的统计/参数提交状态与最后活跃时间，
    管理端据此定位卡住任务的掉线节点（如拔线 30 分钟演练）。
    P2.3 多租户：非本院发起任务对租户管理员按 404 隐藏。
    """
    job = await _get_job(db, job_id)
    await _assert_owned_job(job, _tenant_scope(actor))
    d = _job_to_dict(job)
    member_ids = json.loads(job.expected_nodes)
    updates = (await db.execute(
        select(DistributedUpdate).where(DistributedUpdate.job_id == job.id)
    )).scalars().all()
    last_update_round: dict[str, int] = {}
    moments_sent: set[str] = set()
    for u in updates:
        if u.kind == "moments":
            moments_sent.add(u.node_id)
        elif u.kind == "update":
            last_update_round[u.node_id] = max(u.round_idx, last_update_round.get(u.node_id, 0))
    nodes = (await db.execute(
        select(FederatedNode).where(FederatedNode.id.in_(member_ids))
    )).scalars().all()
    seen = {n.id: n for n in nodes}
    d["progress"] = [{
        "node_id": nid,
        "name": seen[nid].name if nid in seen else nid,
        "moments_submitted": nid in moments_sent,
        "last_update_round": last_update_round.get(nid),
        "last_seen_at": (seen[nid].last_seen_at.isoformat()
                         if nid in seen and seen[nid].last_seen_at else None),
    } for nid in member_ids]
    return d


# ---------------------------------------------------------------------------
# 管理面：任务暂停 / 恢复（P1.3 断点续训）
# ---------------------------------------------------------------------------

@router.post("/jobs/{job_id}/pause")
async def pause_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """手动暂停任务（节点拔线检修/窗口期）：running→paused、collecting→paused。

    paused 期间节点面 assignment 仍可拉取（状态 paused，Agent 轮询自然等待），
    参数/统计上行被 409 拒绝；恢复用 /resume，从 current_round 断点续训。
    P2.3 多租户：租户管理员仅可暂停本院发起任务（非本院 404）。
    """
    job = await _get_job(db, job_id)
    await _assert_owned_job(job, _tenant_scope(actor))
    if job.status not in ("collecting", "running"):
        raise HTTPException(status_code=409, detail=f"任务当前 {job.status}，仅进行中任务可暂停")
    job.status = "paused"
    await db.commit()
    logger.warning("联邦任务已暂停: %s（round=%s）", job.id, job.current_round)
    return {"status": "paused", "round": job.current_round,
            "detail": "任务已暂停；节点 Agent 将轮询等待，恢复后从当前轮续训"}


@router.post("/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    actor: dict = Depends(require_roles("hospital_admin", "operator")),
):
    """恢复暂停任务：按 current_round 回到原阶段（0=collecting，≥1=running）。

    断点续训语义：已提交参数与聚合状态（scaler/global_coef）在暂停期间原样保留，
    恢复后未提交节点补齐本轮即可推进——最终结果与不间断运行一致（有测试背书）。
    P2.3 多租户：租户管理员仅可恢复本院发起任务（非本院 404）。
    """
    job = await _get_job(db, job_id)
    await _assert_owned_job(job, _tenant_scope(actor))
    if job.status != "paused":
        raise HTTPException(status_code=409, detail=f"任务当前 {job.status}，仅暂停中任务可恢复")
    job.status = "collecting" if job.current_round == 0 else "running"
    await db.commit()
    logger.warning("联邦任务已恢复: %s → %s（round=%s）", job.id, job.status, job.current_round)
    return {"status": job.status, "round": job.current_round,
            "detail": "任务已恢复，节点将从当前轮断点续训"}


# ---------------------------------------------------------------------------
# 节点面：任务发现 / assignment / 参数上行（机器凭证，恒强制）
# ---------------------------------------------------------------------------

async def _node_ctx(db: AsyncSession, job_id: str,
                    x_node_id: str | None, x_node_token: str | None):
    node = await _require_node(db, x_node_id, x_node_token)
    job = await _get_job(db, job_id)
    _require_member(job, node)
    return node, job


@router.get("/my-jobs")
async def my_jobs(
    db: AsyncSession = Depends(get_db),
    x_node_id: str | None = Header(None, alias="X-Node-Id"),
    x_node_token: str | None = Header(None, alias="X-Node-Token"),
):
    """节点自动发现被指派的任务（Agent 未配置 NODE_JOB_ID 时用）。"""
    node = await _require_node(db, x_node_id, x_node_token)
    jobs = (await db.execute(
        select(DistributedJob).order_by(DistributedJob.created_at.desc())
    )).scalars().all()
    return [_job_to_dict(j, include_result=False) for j in jobs
            if node.id in json.loads(j.expected_nodes)]


@router.get("/jobs/{job_id}/assignment")
async def get_assignment(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    x_node_id: str | None = Header(None, alias="X-Node-Id"),
    x_node_token: str | None = Header(None, alias="X-Node-Token"),
):
    """节点拉取当前轮任务：配置 + 全局模型 + 全局标准化器（collecting 期间无模型）。"""
    _, job = await _node_ctx(db, job_id, x_node_id, x_node_token)
    running = job.status == "running" and job.global_coef is not None
    return {
        "job_id": job.id, "status": job.status,
        "round": job.current_round, "rounds": job.rounds,
        "local_epochs": job.local_epochs, "dp_sigma": job.dp_sigma,
        "clip_norm": job.clip_norm,
        "global_coef": json.loads(job.global_coef) if running else None,
        "scaler": json.loads(job.scaler_state) if running else None,
        "needs_moments": job.status == "collecting",
    }


@router.post("/jobs/{job_id}/moments")
async def upload_moments(
    job_id: str,
    body: EnvelopeBody,
    db: AsyncSession = Depends(get_db),
    x_node_id: str | None = Header(None, alias="X-Node-Id"),
    x_node_token: str | None = Header(None, alias="X-Node-Token"),
):
    """collecting 阶段：节点上传联邦统计 (n, sum, sum_sq)（加密信封）。

    集齐全部节点后聚合全局标准化器，任务进入 running（第 1 轮开始）。
    """
    node, job = await _node_ctx(db, job_id, x_node_id, x_node_token)
    if job.status != "collecting":
        raise HTTPException(status_code=409, detail="任务已不在统计收集阶段")
    dup = (await db.execute(
        select(DistributedUpdate).where(
            DistributedUpdate.job_id == job.id,
            DistributedUpdate.node_id == node.id,
            DistributedUpdate.round_idx == 0,
            DistributedUpdate.kind == "moments",
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail="该节点统计已提交（幂等保护）")

    data = _open_payload(body.payload)
    try:
        n = int(data["n"])
        sum_, sum_sq = data["sum"], data["sum_sq"]
        if n <= 0 or len(sum_) == 0 or len(sum_) != len(sum_sq):
            raise KeyError
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="信封内容需为 {n, sum[], sum_sq[]} 且长度一致") from e

    # 特征维度全节点一致校验（首个提交定维度）
    existing = (await db.execute(
        select(DistributedUpdate).where(
            DistributedUpdate.job_id == job.id, DistributedUpdate.kind == "moments")
    )).scalars().all()
    if existing:
        first = _open_payload(existing[0].payload_enc)
        if not first.get("sum") or len(first["sum"]) != len(sum_):
            raise HTTPException(status_code=400, detail="特征维度与其他节点不一致")

    db.add(DistributedUpdate(job_id=job.id, node_id=node.id, round_idx=0,
                             kind="moments", payload_enc=body.payload, n_samples=n))
    await record_usage(db, node.hospital_id, "node_updates")  # 计费计量：上行次数（同事务）
    await db.flush()

    submitted = {u.node_id for u in existing} | {node.id}
    if submitted >= set(json.loads(job.expected_nodes)):
        # 集齐 → 聚合全局标准化器 → 进入第 1 轮
        entries = []
        rows = (await db.execute(
            select(DistributedUpdate).where(
                DistributedUpdate.job_id == job.id, DistributedUpdate.kind == "moments")
        )).scalars().all()
        for u in rows:
            d = _open_payload(u.payload_enc)
            entries.append({"n": d["n"], "sum": d["sum"], "sum_sq": d["sum_sq"]})
        mean, std = aggregate_moments(entries)
        n_features = len(mean)
        job.scaler_state = json.dumps({"n": sum(e["n"] for e in entries),
                                       "mean": mean, "std": std})
        job.global_coef = json.dumps([0.0] * (n_features + 1))  # 末位截距
        job.status = "running"
        job.current_round = 1
    await db.commit()
    return {"status": job.status, "round": job.current_round,
            "detail": "统计已接收" if job.status == "collecting" else "全部节点统计已聚合，训练开始"}


@router.post("/jobs/{job_id}/rounds/{round_no}/update")
async def upload_update(
    job_id: str,
    round_no: int,
    body: EnvelopeBody,
    db: AsyncSession = Depends(get_db),
    x_node_id: str | None = Header(None, alias="X-Node-Id"),
    x_node_token: str | None = Header(None, alias="X-Node-Token"),
):
    """running 阶段：节点上传本轮本地训练参数（加密信封 {coef, n, local_auc}）。

    集齐后按样本量加权聚合（FedAvg）推进下一轮；最后一轮聚合即任务完成，
    结果接入任务级存证链。明文参数即时解密聚合、落库仅存密文。
    """
    node, job = await _node_ctx(db, job_id, x_node_id, x_node_token)
    if job.status != "running":
        raise HTTPException(status_code=409, detail=f"任务不在训练阶段（当前 {job.status}）")
    if round_no != job.current_round:
        raise HTTPException(status_code=409, detail=f"当前轮次为 {job.current_round}，拒绝第 {round_no} 轮提交")
    dup = (await db.execute(
        select(DistributedUpdate).where(
            DistributedUpdate.job_id == job.id,
            DistributedUpdate.node_id == node.id,
            DistributedUpdate.round_idx == round_no,
            DistributedUpdate.kind == "update",
        )
    )).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status_code=409, detail="该节点本轮参数已提交（幂等保护）")

    data = _open_payload(body.payload)
    coef = data.get("coef")
    n = data.get("n")
    local_auc = data.get("local_auc")
    expected_len = len(json.loads(job.global_coef or "[]"))
    if (not isinstance(coef, list) or len(coef) != expected_len
            or not isinstance(n, int) or n <= 0 or not isinstance(local_auc, (int, float))):
        raise HTTPException(status_code=400, detail="信封内容需为 {coef[], n>0, local_auc} 且参数维度一致")

    # P1.3 恶意参数方向检测（落库前拦截：拒绝的参数不进 DistributedUpdate 表）
    _check_update_sanity(job, coef)

    db.add(DistributedUpdate(job_id=job.id, node_id=node.id, round_idx=round_no,
                             kind="update", payload_enc=body.payload,
                             n_samples=n, local_auc=float(local_auc)))
    await record_usage(db, node.hospital_id, "node_updates")  # 计费计量：上行次数（同事务）
    await db.flush()

    rows = (await db.execute(
        select(DistributedUpdate).where(
            DistributedUpdate.job_id == job.id,
            DistributedUpdate.round_idx == round_no,
            DistributedUpdate.kind == "update")
    )).scalars().all()
    if {u.node_id for u in rows} < set(json.loads(job.expected_nodes)):
        await db.commit()
        return {"status": "running", "round": round_no,
                "detail": f"本轮参数已接收（等待其余节点，已收 {len(rows)}/{len(job.expected_nodes)}）"}

    # 集齐 → FedAvg 加权聚合（即时解密，密文不留明文）
    states, weights = [], []
    for u in rows:
        d = _open_payload(u.payload_enc)
        states.append(d["coef"])
        weights.append(d["n"])
    new_coef = aggregate_coef(states, weights)

    if round_no < job.rounds:
        job.global_coef = json.dumps(new_coef)
        job.current_round = round_no + 1
        await db.commit()
        return {"status": "running", "round": job.current_round,
                "detail": f"第 {round_no} 轮聚合完成，进入第 {job.current_round} 轮"}

    # 最后一轮：按节点验证集样本量加权 AUC 收口 → 存证链 → done
    last_round = {(u.node_id): u for u in rows}
    per_node = []
    for u in rows:
        per_node.append({"node_id": u.node_id, "n": u.n_samples,
                         "local_auc": round(u.local_auc, 4)})
    result = {
        "final_auc": round(weighted_auc([{"n": u.n_samples, "local_auc": u.local_auc} for u in rows]), 4),
        "per_node": per_node,
        "rounds": job.rounds, "local_epochs": job.local_epochs,
        "dp_sigma": job.dp_sigma, "clip_norm": job.clip_norm,
        "total_samples": int(sum(last_round[u.node_id].n_samples for u in rows)),
        "privacy": privacy_report(job.dp_sigma, job.clip_norm, job.rounds,
                                  delta=settings.DP_ACCOUNTANT_DELTA,
                                  local_epochs=job.local_epochs),
        "note": "AUC 为各节点本地验证集聚合指标；原始数据未离开任何节点",
    }
    job.result = json.dumps(result, ensure_ascii=False)
    job.status = "done"
    if job.created_at:
        # SQLite 存取为 naive UTC，统一按 UTC 补时区后再相减
        created = job.created_at if job.created_at.tzinfo else job.created_at.replace(tzinfo=UTC)
        job.duration_ms = int((datetime.now(UTC) - created).total_seconds() * 1000)
    await _append_chain(db, job)
    await db.commit()
    logger.info("分布式联邦任务完成: %s final_auc=%.4f", job.id, result["final_auc"])
    return {"status": "done", "round": round_no, "result": result}
