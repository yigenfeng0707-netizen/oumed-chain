"""管理后台 API（超级管理员）。

提供：
- POST /api/admin/login        管理员登录（账号密码 → token）
- GET  /api/admin/overview     全用户使用概况 + 全局统计（推送/营销分群用）
- GET  /api/admin/users              用户列表（含 RBAC 角色，P0-1.1）
- GET  /api/admin/users/{id}/profile  单用户画像详情
- PUT  /api/admin/users/{id}/role    设置用户 RBAC 角色（P0-1.1 多角色）
- GET  /api/admin/security/denials    越权访问审计日志（超管或监管/运营角色）

鉴权：登录后签发 X-Admin-Token，后续请求携带该校验；RBAC 端点另接受
携带对应角色的 X-User-Token（见 app.deps.require_roles）。
账号密码通过环境变量 ADMIN_USERNAME / ADMIN_PASSWORD 配置。
说明：Demo 阶段无真实登录日志，「使用情况」基于对话/脑电/影像等活动记录统计。
"""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, metrics
from app.auth import ROLES
from app.config import settings
from app.database import get_db
from app.deps import require_roles
from app.models import (
    BodyRecord,
    ChatConversation,
    ChatMessage,
    EEGRecord,
    Hospital,
    ImagingRecord,
    MedicalRecord,
    User,
)
from app.services import chain_anchor, login_guard, tenant_usage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["管理后台"])

# 默认管理员账号（生产环境务必通过环境变量/ .env 覆盖）
default_admin_username = "admin"
default_admin_password = "瓯医数链@2026"


def _admin_credentials() -> tuple[str, str]:
    """从 Settings（env / .env）读取管理员账号密码。"""
    return (
        settings.ADMIN_USERNAME or default_admin_username,
        settings.ADMIN_PASSWORD or default_admin_password,
    )


def _issue_admin_token(username: str) -> str:
    """无状态 token：用户名 + 密钥的哈希（重启后仍有效，改密/换密钥即失效）。"""
    raw = f"admin|{username}|{settings.YIBAO_ADMIN_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AdminLoginRequest(BaseModel):
    username: str
    password: str


async def require_admin(x_admin_token: str | None = Header(None, alias="X-Admin-Token")) -> str:
    """管理员鉴权依赖：校验 X-Admin-Token。"""
    username, _ = _admin_credentials()
    expected = _issue_admin_token(username)
    if not x_admin_token or x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员未登录或 token 失效",
            headers={"WWW-Authenticate": "AdminToken"},
        )
    return username


@router.post("/login")
async def admin_login(payload: AdminLoginRequest):
    """管理员登录，返回 token（有效期 24h，前端存 sessionStorage）。

    等保三级·入侵防范（P0-1.2）：生产模式启用账号级失败锁定（指数退避）；
    失败同时计入撞库滑窗指标 + Webhook 告警（既有机制）。
    """
    guard_key = f"admin:{payload.username}"
    if not settings.DEMO_MODE:
        remain = login_guard.is_locked(guard_key)
        if remain > 0:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"登录失败次数过多，账号已临时锁定，请 {remain} 秒后重试",
            )
    username, password = _admin_credentials()
    if payload.username != username or payload.password != password:
        metrics.observe_admin_login_failure()  # 监控告警：撞库信号（指标）
        if not settings.DEMO_MODE:
            login_guard.record_failure(guard_key)
        try:
            from app.services import alerting
            await asyncio.to_thread(alerting.record_admin_login_failure, payload.username)
        except Exception:
            pass  # 告警触达失败不影响拒绝响应
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not settings.DEMO_MODE:
        login_guard.record_success(guard_key)
    return {
        "token": _issue_admin_token(username),
        "username": username,
        "role": "super_admin",
        "expires_in": 86400,
        "message": "登录成功",
    }


def _fmt_dt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return str(value)


@router.get("/overview")
async def admin_overview(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """全用户使用概况：基础信息 + 各模块使用量 + 最近活跃时间 + 慢病分布（推送分群用）。"""
    users = await crud.get_users(db, limit=500)

    # —— 各模块使用量（SQL 聚合）——
    conv_counts: dict[int, int] = {}
    for uid, cnt in await db.execute(
        select(ChatConversation.user_id, func.count(ChatConversation.id)).group_by(
            ChatConversation.user_id
        )
    ):
        conv_counts[uid] = cnt

    msg_counts: dict[int, int] = {}
    last_active: dict[int, datetime] = {}
    for uid, cnt, last_dt in await db.execute(
        select(
            ChatConversation.user_id,
            func.count(ChatMessage.id),
            func.max(ChatMessage.created_at),
        )
        .join(ChatMessage, ChatMessage.conversation_id == ChatConversation.id)
        .group_by(ChatConversation.user_id)
    ):
        msg_counts[uid] = cnt
        if last_dt is not None:
            last_active[uid] = last_dt

    eeg_counts: dict[int, int] = {}
    for uid, cnt in await db.execute(
        select(EEGRecord.user_id, func.count(EEGRecord.id)).group_by(EEGRecord.user_id)
    ):
        eeg_counts[uid] = cnt

    imaging_counts: dict[int, int] = {}
    for uid, cnt in await db.execute(
        select(ImagingRecord.user_id, func.count(ImagingRecord.id)).group_by(
            ImagingRecord.user_id
        )
    ):
        imaging_counts[uid] = cnt

    body_counts: dict[int, int] = {}
    for uid, cnt in await db.execute(
        select(BodyRecord.user_id, func.count(BodyRecord.id)).group_by(BodyRecord.user_id)
    ):
        body_counts[uid] = cnt

    visit_counts: dict[int, int] = {}
    for uid, cnt in await db.execute(
        select(MedicalRecord.user_id, func.count(MedicalRecord.id)).group_by(
            MedicalRecord.user_id
        )
    ):
        visit_counts[uid] = cnt

    now_utc = datetime.now(UTC)
    week_ago = now_utc - timedelta(days=7)

    # —— 慢病分布（来自购药分类推断，供精准推送分群）——
    condition_dist: dict[str, int] = {}
    user_rows = []
    active_7d = 0
    for user in users:
        profile = await crud.get_user_health_profile(db, user.id)
        conditions = profile.get("chronic_diseases", []) if profile.get("found") else []
        for c in conditions:
            condition_dist[c] = condition_dist.get(c, 0) + 1

        last = last_active.get(user.id)
        last_iso = _fmt_dt(last)
        is_active_7d = bool(last and last.replace(tzinfo=UTC if last.tzinfo is None else last.tzinfo) >= week_ago)
        if is_active_7d:
            active_7d += 1

        user_rows.append(
            {
                "id": user.id,
                "public_id": f"user_{user.id:03d}",
                "name": user.name,
                "age": user.age,
                "gender": user.gender,
                "city": user.city,
                "insurance_type": user.insurance_type,
                "employee_status": user.employee_status,
                "conditions": conditions,
                "usage": {
                    "conversations": conv_counts.get(user.id, 0),
                    "messages": msg_counts.get(user.id, 0),
                    "eeg_sessions": eeg_counts.get(user.id, 0),
                    "imaging_studies": imaging_counts.get(user.id, 0),
                    "body_records": body_counts.get(user.id, 0),
                    "medical_visits": visit_counts.get(user.id, 0),
                },
                "last_active_at": last_iso,
                "active_7d": is_active_7d,
            }
        )

    # 按最近活跃排序（无活动排最后）
    user_rows.sort(key=lambda r: (r["last_active_at"] is None, r["last_active_at"] or ""), reverse=False)
    user_rows.sort(key=lambda r: r["last_active_at"] is None)

    return {
        "generated_at": now_utc.isoformat(),
        "admin": admin,
        "global_stats": {
            "total_users": len(users),
            "active_users_7d": active_7d,
            "total_conversations": sum(conv_counts.values()),
            "total_messages": sum(msg_counts.values()),
            "total_eeg_sessions": sum(eeg_counts.values()),
            "total_imaging_studies": sum(imaging_counts.values()),
            "condition_distribution": condition_dist,
        },
        "users": user_rows,
    }


@router.get("/users")
async def admin_users(
    actor: dict = Depends(require_roles("operator", "hospital_admin")),
    db: AsyncSession = Depends(get_db),
    limit: int = 200,
):
    """用户列表（含 RBAC 角色）：管理后台用户与角色管理用（P0-1.1）。

    P2.3 多租户：hospital_admin 仅见本院用户（scope 到本院，含无租户平台用户
    均不可见）；operator/超管全局。返回体含 scope 说明。
    """
    stmt = select(User).order_by(User.id).limit(min(limit, 1000))
    scope = None
    if actor["role"] == "hospital_admin":
        scope = actor.get("hospital_id")
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="医院管理员账号未绑定租户，请联系平台分配归属医院后重新登录")
        stmt = stmt.where(User.hospital_id == scope)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "total": len(rows),
        "roles": list(ROLES),
        "scope_hospital_id": scope,
        "users": [
            {
                "id": u.id,
                "public_id": f"user_{u.id:03d}",
                "name": u.name,
                "email": u.email,
                "role": u.role or "member",
                "hospital_id": u.hospital_id,
                "city": u.city,
                "created_at": _fmt_dt(u.created_at),
            }
            for u in rows
        ],
    }


class RoleUpdateRequest(BaseModel):
    role: str
    # P2.3 多租户：可选同时绑定医院租户（hospital_admin 建议必绑，否则租户端点 403）
    hospital_id: int | None = None

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        v = v.strip()
        if v not in ROLES:
            raise ValueError(f"非法角色，可选：{'/'.join(ROLES)}")
        return v


@router.put("/users/{user_id}/role")
async def admin_set_user_role(
    user_id: str,
    payload: RoleUpdateRequest,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """设置用户 RBAC 角色（仅超管；P0-1.1 多角色）。

    P2.3 多租户：可选 hospital_id 同时绑定医院租户（写入签名 token 后重新
    登录生效）；绑定前校验租户存在。无状态 token 权衡：角色/租户写入签名
    payload，被改用户重新登录后生效；如需立即生效可轮换 YIBAO_SESSION_SECRET
    全量失效会话。
    """
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if payload.hospital_id is not None and await db.get(Hospital, payload.hospital_id) is None:
        raise HTTPException(status_code=400, detail=f"医院租户 {payload.hospital_id} 不存在")
    old_role = user.role or "member"
    user.role = payload.role
    if payload.hospital_id is not None:
        user.hospital_id = payload.hospital_id
    await db.commit()
    logger.info("RBAC 角色变更: %s %s → %s（租户=%s，操作者: %s）",
                user.public_id if hasattr(user, "public_id") else f"user_{user.id:03d}",
                old_role, payload.role, user.hospital_id, admin)
    return {
        "user_id": user.id,
        "public_id": f"user_{user.id:03d}",
        "name": user.name,
        "old_role": old_role,
        "role": user.role,
        "hospital_id": user.hospital_id,
        "note": "重新登录后新角色/租户归属生效（token 无状态签名）",
    }


@router.get("/users/{user_id}/profile")
async def admin_user_profile(
    user_id: str,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """单用户画像详情：健康画像 + 近期对话 + EEG/影像历史 + 档案摘要。"""
    user = await crud.get_user(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    profile = await crud.get_user_health_profile(db, user.id)

    # 近期对话（含最近一条消息预览）
    conversations = (
        (await db.execute(
            select(ChatConversation)
            .where(ChatConversation.user_id == user.id)
            .order_by(desc(ChatConversation.updated_at))
            .limit(10)
        ))
        .scalars()
        .all()
    )
    conv_rows = []
    for conv in conversations:
        msgs = (
            (await db.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conv.id)
                .order_by(desc(ChatMessage.id))
                .limit(1)
            ))
            .scalars()
            .all()
        )
        conv_rows.append(
            {
                "id": conv.id,
                "title": conv.title,
                "updated_at": _fmt_dt(conv.updated_at),
                "message_count": (
                    (
                        await db.execute(
                            select(func.count(ChatMessage.id)).where(
                                ChatMessage.conversation_id == conv.id
                            )
                        )
                    ).scalar()
                    or 0
                ),
                "last_message": msgs[0].content[:120] if msgs else "",
            }
        )

    # EEG 历史（摘要）
    eeg_rows = []
    for r in await crud.get_eeg_records(db, user.id, limit=10):
        d = crud.eeg_record_to_dict(r)
        eeg_rows.append(
            {
                "recorded_at": d["recorded_at"],
                "mental_state_label": d["mental_state_label"],
                "alert_count": d["alert_count"],
                "policy_link_count": d["policy_link_count"],
                "summary": (d.get("summary") or "")[:160],
            }
        )

    # 影像历史（摘要）
    imaging_rows = []
    for r in await crud.get_imaging_records(db, user.id, limit=10):
        d = crud.imaging_record_to_dict(r)
        imaging_rows.append(
            {
                "recorded_at": d["recorded_at"],
                "study_type": d["study_type"],
                "risk_level": d["risk_level"],
                "finding_count": len(d.get("findings") or []),
                "policy_link_count": d["policy_link_count"],
            }
        )

    # 档案器官摘要
    organ_summary = await crud.get_body_organ_summary(db, user.id)

    return {
        "user_id": user.id,
        "basic": {
            "name": user.name,
            "age": user.age,
            "gender": user.gender,
            "city": user.city,
            "insurance_type": user.insurance_type,
            "employee_status": user.employee_status,
            "registered_at": _fmt_dt(user.created_at),
        },
        "health_profile": profile,
        "conversations": conv_rows,
        "eeg_history": eeg_rows,
        "imaging_history": imaging_rows,
        "body_organ_summary": organ_summary,
    }


_DENIAL_REASON_LABELS = {
    "missing_session": "未携带有效会话",
    "missing_or_invalid_user_token": "用户 token 缺失或无效",
    "cross_user_access": "跨用户越权访问",
}


@router.get("/security/denials")
async def admin_security_denials(
    identity: dict = Depends(require_roles("regulator", "operator")),  # 超管或监管/运营角色
    db: AsyncSession = Depends(get_db),
    user_id: str | None = None,
    status_code: int | None = None,
    limit: int = 100,
):
    """越权访问审计：严格模式（DEMO_MODE=false）下的 401/403 拒绝记录。

    超管（X-Admin-Token）或携带 regulator/operator 角色的用户可查（P0-1.1 RBAC）。
    演示模式不产生拒绝（全部放行），列表为空属正常。
    """
    logs = await crud.get_access_denials(
        db, target_user_id=user_id, status_code=status_code, limit=min(limit, 500)
    )
    rows = [
        {
            "id": log.id,
            "ts": _fmt_dt(log.ts),
            "method": log.method,
            "path": log.path,
            "target_user_id": log.target_user_id,
            "status_code": log.status_code,
            "reason": log.reason,
            "reason_label": _DENIAL_REASON_LABELS.get(log.reason, log.reason),
            "client_ip": log.client_ip,
            "token_present": log.token_present,
        }
        for log in logs
    ]
    return {
        "total": len(rows),
        "demo_mode": settings.DEMO_MODE,
        "logs": rows,
    }


# ============================================================
# 存证链外部锚定（P2-3.4）：链尖摘要 → RFC 3161 可信时间戳
# ============================================================

@router.post("/security/anchor")
async def admin_chain_anchor_create(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """立即对存证链链尖做一次外部锚定（RFC 3161 TSA）。

    TSA 不可达时降级为 offline 留痕（链尖哈希仍入库，可事后补锚）。
    """
    anchor = await chain_anchor.create_anchor(db, tsa_url=settings.CHAIN_ANCHOR_TSA_URL or None)
    return chain_anchor.anchor_to_dict(anchor, include_token=True)


@router.get("/security/anchors")
async def admin_chain_anchors(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    """存证链锚定历史（管理员视图，含时间戳令牌）。"""
    anchors = await crud.get_chain_anchors(db, limit=min(limit, 500))
    return {
        "total": len(anchors),
        "tsa_url": settings.CHAIN_ANCHOR_TSA_URL,
        "anchors": [chain_anchor.anchor_to_dict(a, include_token=True) for a in anchors],
    }


# ============================================================
# 支付对账（支付宝当面付：Agent 微支付 / 数据产品结算）
# ============================================================

@router.get("/payments")
async def admin_payments(
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = 100,
):
    """支付订单对账：订单列表 + 已收总额/分渠道统计。"""
    from app.routers.payments import _order_to_dict

    orders = await crud.get_payment_orders(db, limit=min(limit, 500))
    paid = [o for o in orders if o.status == "paid"]
    return {
        "total": len(orders),
        "mode": settings.ALIPAY_MODE,
        "paid_count": len(paid),
        "revenue_cents": sum(o.amount_cents for o in paid),
        "orders": [_order_to_dict(o) for o in orders],
    }


# ============================================================
# 医院租户管理（P2.3 多租户）：CRUD + 计费计量报表
# ============================================================

class HospitalCreateRequest(BaseModel):
    name: str
    code: str

    @field_validator("name", "code")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v


class HospitalStatusRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in ("active", "disabled"):
            raise ValueError("status 需为 active/disabled")
        return v


@router.post("/hospitals")
async def admin_create_hospital(
    payload: HospitalCreateRequest,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """创建医院租户（仅超管）：code 全局唯一。"""
    exists = (await db.execute(
        select(Hospital).where(Hospital.code == payload.code)
    )).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"租户编码 {payload.code} 已存在")
    hospital = Hospital(name=payload.name, code=payload.code)
    db.add(hospital)
    await db.commit()
    logger.info("医院租户创建: %s (%s, id=%s, 操作者: %s)",
                hospital.name, hospital.code, hospital.id, admin)
    return {"id": hospital.id, "name": hospital.name, "code": hospital.code,
            "status": hospital.status,
            "created_at": _fmt_dt(hospital.created_at)}


@router.get("/hospitals")
async def admin_list_hospitals(
    actor: dict = Depends(require_roles("operator", "hospital_admin")),
    db: AsyncSession = Depends(get_db),
):
    """医院租户列表（含各租户用量汇总）：hospital_admin 仅见本院。"""
    stmt = select(Hospital).order_by(Hospital.id)
    if actor["role"] == "hospital_admin":
        scope = actor.get("hospital_id")
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="医院管理员账号未绑定租户，请联系平台分配归属医院后重新登录")
        stmt = stmt.where(Hospital.id == scope)
    hospitals = (await db.execute(stmt)).scalars().all()
    usage = {u["hospital_id"]: u["totals"] for u in await tenant_usage.usage_summary(db)}
    return {
        "scope_hospital_id": actor.get("hospital_id")
        if actor["role"] == "hospital_admin" else None,
        "hospitals": [{
            "id": h.id, "name": h.name, "code": h.code, "status": h.status,
            "usage_totals": usage.get(h.id, {}),
            "created_at": _fmt_dt(h.created_at),
        } for h in hospitals],
    }


@router.put("/hospitals/{hospital_id}/status")
async def admin_set_hospital_status(
    hospital_id: int,
    payload: HospitalStatusRequest,
    admin: str = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """启用/停用医院租户（仅超管）：停用为管理面标记，节点凭证校验不受影响。"""
    hospital = await db.get(Hospital, hospital_id)
    if hospital is None:
        raise HTTPException(status_code=404, detail="医院租户不存在")
    hospital.status = payload.status
    await db.commit()
    logger.info("医院租户状态变更: %s → %s（操作者: %s）", hospital.code, payload.status, admin)
    return {"id": hospital.id, "name": hospital.name, "code": hospital.code,
            "status": hospital.status}


@router.get("/tenants/usage")
async def admin_tenant_usage(
    actor: dict = Depends(require_roles("operator", "hospital_admin")),
    db: AsyncSession = Depends(get_db),
    hospital_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """租户计费计量报表（P2.3 验收：计量报表准确）。

    - hospital_admin：强制本院 scope（hospital_id 参数被忽略，永远只看本院）；
    - operator/超管：hospital_id 可选（None=全部租户），支持 date_from/date_to 过滤。
    """
    scope = None
    if actor["role"] == "hospital_admin":
        scope = actor.get("hospital_id")
        if scope is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="医院管理员账号未绑定租户，请联系平台分配归属医院后重新登录")
    elif hospital_id is not None:
        scope = hospital_id
    report = await tenant_usage.usage_report(db, hospital_id=scope,
                                             date_from=date_from, date_to=date_to)
    return {
        "scope_hospital_id": scope,
        "date_from": date_from,
        "date_to": date_to,
        **report,
    }
