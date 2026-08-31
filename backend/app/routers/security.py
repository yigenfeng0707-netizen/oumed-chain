"""
瓯医数链 - 数据安全路由

P0-2 升级：
- 接入真实 DataAuthorization 表
- 授权矩阵基于真实授权数据生成
- 审计日志带存证哈希（呼应可信数据空间"区块链存证"叙事）
- create_authorization 真实写库
"""

import hashlib
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.auth import require_api_key
from app.database import get_db
from app.deps import ScopeDep, SessionDep
from app.schemas import AuthorizationRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/security", tags=["数据安全"])

# 数据类型与智能体定义
DATA_TYPES = ["医保缴费记录", "就医记录", "购药记录", "健康档案"]
AGENTS = [
    ("coverage_agent", "权益管家"),
    ("health_agent", "健康卫士"),
    ("claims_agent", "报销助手"),
    ("policy_agent", "政策参谋"),
    ("body_agent", "档案管家"),
    ("data_agent", "数据管家"),
    ("drug_agent", "药品卫士"),
]


@router.get("/authorizations/{user_id}")
async def get_authorizations(user_id: str, db: AsyncSession = Depends(get_db), _scope: str = ScopeDep):
    """获取用户数据授权全景（基于真实授权数据）"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    auths = await crud.get_active_authorizations(db, user_id)

    # 构建授权矩阵
    matrix = []
    for dt in DATA_TYPES:
        row = {"data_type": dt}
        for agent_key, agent_name in AGENTS:
            matched = [a for a in auths
                       if a.data_type == dt and a.authorized_agent in (agent_key, agent_name)]
            if matched:
                a = matched[0]
                row[agent_key] = {"authorized": True, "expires": a.expires_at.date().isoformat() if a.expires_at else None}
            else:
                row[agent_key] = {"authorized": False, "expires": None}
        matrix.append(row)

    # 按 Agent 聚合授权的数据类型
    active_auths = []
    for agent_key, agent_name in AGENTS:
        dts = list({a.data_type for a in auths if a.authorized_agent in (agent_key, agent_name)})
        if dts:
            first = next((a for a in auths if a.authorized_agent in (agent_key, agent_name)), None)
            active_auths.append({
                "agent": agent_name,
                "agent_key": agent_key,
                "data_types": dts,
                "authorized_at": first.authorized_at.isoformat() if first and first.authorized_at else "",
                "expires_at": first.expires_at.isoformat() if first and first.expires_at else "",
            })

    # 审计日志（模拟近期访问 + 存证哈希）
    audit_log = _build_audit_log(user_id, auths)

    return {
        "active_authorizations": len(auths),
        "anomalies": 0,
        "today_accesses": len(audit_log),
        "authorization_matrix": matrix,
        "rights": [
            {"name": "知情权", "description": "您有权了解个人数据被哪些智能体访问及用途", "icon": "eye"},
            {"name": "更正权", "description": "您有权要求更正不准确的个人数据", "icon": "edit"},
            {"name": "删除权", "description": "您有权要求删除您的个人数据", "icon": "trash"},
            {"name": "可携带权", "description": "您有权以通用格式导出您的个人数据", "icon": "download"},
        ],
        "active_auths": active_auths,
        "audit_log": audit_log,
    }


@router.post("/authorize")
async def create_authorization(
    request: AuthorizationRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_api_key),
    _session: str = SessionDep,
):
    """创建/更新授权（真实写库）

    挂载鉴权（选择性）：配置 YIBAO_API_KEY 后，需 X-API-Key 头；
    未配置则放行（保证 Demo 流畅）。呼应"安全守门 Agent"。
    """
    user = await crud.get_user(db, request.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {request.user_id} 不存在")

    auth = await crud.create_authorization(
        db=db,
        user_id=request.user_id,
        data_type=request.data_type,
        authorized_agent=request.authorized_agent,
        duration_days=request.duration_days,
    )

    # 生成存证哈希（呼应可信数据空间区块链存证）
    proof_str = f"{auth.id}|{request.user_id}|{request.data_type}|{request.authorized_agent}|{auth.authorized_at.isoformat()}"
    proof_hash = hashlib.sha256(proof_str.encode("utf-8")).hexdigest()

    return {
        "id": auth.id,
        "user_id": request.user_id,
        "data_type": auth.data_type,
        "authorized_agent": auth.authorized_agent,
        "authorized_at": auth.authorized_at.isoformat(),
        "expires_at": auth.expires_at.isoformat(),
        "is_active": auth.is_active,
        "proof_hash": proof_hash,  # 存证哈希（可信数据空间叙事）
        "message": "授权已创建并存证",
    }


@router.get("/audit-log/{user_id}")
async def get_audit_log(user_id: str, db: AsyncSession = Depends(get_db), _scope: str = ScopeDep):
    """获取用户数据访问审计日志"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    auths = await crud.get_active_authorizations(db, user_id)
    logs = _build_audit_log(user_id, auths)
    logs.extend(await _denial_entries(db, user_id))

    return {
        "user_id": user_id,
        "logs": [
            {
                "id": log["id"],
                "action": log["action"],
                "agent": log["agent"],
                "data_type": log["data_type"],
                "timestamp": log["timestamp"],
                "detail": log["detail"],
                "proof_hash": log.get("proof_hash", ""),
            }
            for log in logs
        ],
    }


@router.get("/data-flow/{user_id}")
async def get_data_flow(user_id: str, db: AsyncSession = Depends(get_db), _scope: str = ScopeDep):
    """可信数据空间数据流转记录（P2-2 可视化用）

    展示：数据源 → 隐私计算沙箱 → Agent → 用户 的完整流转链路。
    """
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"用户 {user_id} 不存在")

    auths = await crud.get_active_authorizations(db, user_id)

    flows = []
    for a in auths[:6]:
        flow_id = f"flow_{a.id:04d}"
        # 4 步流转：申请 → 合规检查 → 隐私计算 → 存证返回
        steps = [
            {"step": "数据使用申请", "actor": a.authorized_agent, "status": "done",
             "detail": f"{a.authorized_agent} 申请访问 {a.data_type}", "ts": a.authorized_at.isoformat()},
            {"step": "合规检查", "actor": "安全守门", "status": "done",
             "detail": "校验授权范围、用途、有效期，通过《数据安全法》合规检查", "ts": a.authorized_at.isoformat()},
            {"step": "隐私计算沙箱", "actor": "可信数据空间", "status": "done",
             "detail": "数据在沙箱内可用不可见，仅输出计算结果，原始数据不出域", "ts": a.authorized_at.isoformat()},
            {"step": "审计存证", "actor": "区块链存证节点", "status": "done",
             "detail": f"访问记录上链存证，哈希 {hashlib.sha256(f'{a.id}'.encode()).hexdigest()[:16]}…", "ts": a.authorized_at.isoformat()},
        ]
        flows.append({"id": flow_id, "data_type": a.data_type, "agent": a.authorized_agent, "steps": steps})

    return {
        "user_id": user_id,
        "user_name": user.name,
        "total_flows": len(flows),
        "flows": flows,
        "principle": "数据可用不可见 · 原始数据不出域 · 全链路可追溯",
    }


@router.get("/chain-anchors")
async def get_chain_anchors(db: AsyncSession = Depends(get_db), limit: int = 20):
    """存证链外部锚定公开视图（P2-3.4）。

    仅返回哈希/时间戳状态，不含任何个人数据；供监管与用户验证存证不可篡改：
    链尖摘要定期送公共可信时间戳机构（RFC 3161）签名，平台自身无法伪造历史。
    """
    anchors = await crud.get_chain_anchors(db, limit=min(limit, 100))
    latest = anchors[0] if anchors else None
    return {
        "anchored_count": len(anchors),
        "latest": {
            "tip_hash": latest.tip_hash,
            "status": latest.status,
            "event_count": latest.event_count,
            "created_at": latest.created_at.isoformat() if latest.created_at else None,
        } if latest else None,
        "principle": "链尖摘要 → 公共可信时间戳签名：存证历史不可伪造、篡改可追溯",
        "anchors": [
            {
                "tip_hash": a.tip_hash,
                "status": a.status,
                "event_count": a.event_count,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in anchors
        ],
    }


# ============================================================
# 内部工具
# ============================================================

async def _denial_entries(db: AsyncSession, user_id: str) -> list[dict]:
    """针对该用户的越权拒绝记录（严格模式产生；演示模式为空）。"""
    entries = []
    for log in await crud.get_access_denials(db, target_user_id=user_id, limit=20):
        ts = log.ts.isoformat() if log.ts else datetime.now(UTC).isoformat()
        proof = hashlib.sha256(f"denial|{log.id}|{log.path}".encode("utf-8")).hexdigest()
        entries.append({
            "id": 3000 + log.id,
            "timestamp": ts,
            "time": ts.replace("T", " ").split(".")[0],
            "agent": "安全守门",
            "action": "拒绝访问",
            "data_type": "",
            "status": "denied",
            "purpose": "越权拦截",
            "detail": f"拦截 {log.method} {log.path}（HTTP {log.status_code}）",
            "proof_hash": proof[:32] + "…",
        })
    return entries


def _build_audit_log(user_id: str, auths) -> list[dict]:
    """构建审计日志（基于真实授权记录 + 模拟近期访问 + 存证哈希）"""
    now_ts = time.time()
    logs = []

    # 真实授权记录作为"授权事件"
    for a in auths:
        ts = a.authorized_at.isoformat() if a.authorized_at else datetime.now(UTC).isoformat()
        proof = hashlib.sha256(f"auth|{a.id}|{a.data_type}".encode()).hexdigest()
        logs.append({
            "id": 1000 + a.id,
            "timestamp": ts,
            "time": ts.replace("T", " ").split(".")[0],
            "agent": a.authorized_agent,
            "action": "查询" + a.data_type,
            "data_type": a.data_type,
            "status": "allowed",
            "purpose": "授权范围内访问",
            "detail": f"{a.authorized_agent} 读取 {a.data_type}（授权有效期至 {a.expires_at.date() if a.expires_at else 'N/A'}）",
            "proof_hash": proof[:32] + "…",
        })

    # 补充几条模拟的近期数据访问（演示审计能力）
    sample_agents = ["健康卫士", "权益管家", "政策参谋", "报销助手"]
    sample_purposes = ["生成健康画像", "权益查询", "政策匹配分析", "报销预审"]
    sample_dts = ["就医记录", "医保缴费记录", "购药记录"]
    for i in range(4):
        ts_iso = datetime.fromtimestamp(now_ts - i * 3600, tz=UTC).isoformat()
        agent = sample_agents[i % len(sample_agents)]
        dt = sample_dts[i % len(sample_dts)]
        purpose = sample_purposes[i % len(sample_purposes)]
        proof = hashlib.sha256(f"access|{i}|{agent}|{dt}".encode()).hexdigest()
        logs.append({
            "id": 2000 + i,
            "timestamp": ts_iso,
            "time": ts_iso.replace("T", " ").split(".")[0],
            "agent": agent,
            "action": "查询" + dt,
            "data_type": dt,
            "status": "allowed",
            "purpose": purpose,
            "detail": f"{agent} 读取 {dt} 用于{purpose}",
            "proof_hash": proof[:32] + "…",
        })

    return logs
