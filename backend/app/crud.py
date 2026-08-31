"""
MedSignal - 统一数据访问层 (CRUD)

所有 Router / Service 通过本模块查询数据库，避免直接操作 session。
所有函数均为 async，接收 AsyncSession，返回 ORM 对象或标量。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    BodyDocument,
    BodyRecord,
    ChatConversation,
    ChatMessage,
    DataAuthorization,
    EEGRecord,
    ImagingRecord,
    InsuranceRecord,
    MedicalRecord,
    MedicationRecord,
    PolicyDocument,
    User,
)
from app.services.body.taxonomy import label_of as _organ_label

logger = logging.getLogger(__name__)


# ============================================================
# 用户
# ============================================================

async def get_user(db: AsyncSession, user_id: str | int) -> User | None:
    """根据 user_id 查询用户。支持数字 id 或 'user_001' 形式。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(select(User).where(User.id == uid))
    return result.scalar_one_or_none()


async def get_users(db: AsyncSession, limit: int = 50) -> list[User]:
    """获取用户列表（多用户切换 Demo 用）。"""
    result = await db.execute(select(User).order_by(User.id).limit(limit))
    return list(result.scalars().all())


async def get_user_count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.id)))
    return int(result.scalar() or 0)


async def create_user(db: AsyncSession, **values) -> User:
    user = User(**values)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_conversation(
    db: AsyncSession, conversation_id: str
) -> ChatConversation | None:
    result = await db.execute(
        select(ChatConversation).where(ChatConversation.id == conversation_id)
    )
    return result.scalar_one_or_none()


async def create_conversation(
    db: AsyncSession, conversation_id: str, user_id: str | int, title: str
) -> ChatConversation:
    conversation = ChatConversation(
        id=conversation_id,
        user_id=_normalize_user_id(user_id),
        title=title[:100] or "新对话",
    )
    db.add(conversation)
    await db.flush()
    return conversation


async def append_chat_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    agent_type: str | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        agent_type=agent_type,
    )
    db.add(message)
    conversation = await get_conversation(db, conversation_id)
    if conversation:
        conversation.updated_at = datetime.now(UTC)
    await db.flush()
    return message


async def get_conversation_messages(
    db: AsyncSession, conversation_id: str, limit: int = 100
) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id)
        .limit(limit)
    )
    return list(result.scalars().all())


# ============================================================
# 医保缴费记录
# ============================================================

async def get_insurance_records(
    db: AsyncSession, user_id: str | int, limit: int = 24
) -> list[InsuranceRecord]:
    """获取用户近 N 个月的缴费记录（按时间倒序）。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(InsuranceRecord)
        .where(InsuranceRecord.user_id == uid)
        .order_by(desc(InsuranceRecord.year), desc(InsuranceRecord.month))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_payment_years(db: AsyncSession, user_id: str | int) -> int:
    """累计缴费月数 → 折算年数（向下取整）。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(func.count(InsuranceRecord.id)).where(InsuranceRecord.user_id == uid)
    )
    months = int(result.scalar() or 0)
    return months


# ============================================================
# 就诊记录
# ============================================================

async def get_medical_records(
    db: AsyncSession, user_id: str | int, limit: int = 50
) -> list[MedicalRecord]:
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(MedicalRecord)
        .where(MedicalRecord.user_id == uid)
        .order_by(desc(MedicalRecord.date))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_medical_records_in_range(
    db: AsyncSession, user_id: str | int, months: int = 6
) -> list[MedicalRecord]:
    """近 N 个月内的就诊记录（用于健康评分/政策匹配）。"""
    records = await get_medical_records(db=db, user_id=user_id, limit=200)
    # 过滤时间窗口（naive datetime 视为 UTC）
    import time
    cutoff = time.time() - months * 30 * 86400
    out = []
    for r in records:
        try:
            ts = r.date.replace(tzinfo=UTC).timestamp() if r.date.tzinfo is None else r.date.timestamp()
        except Exception:
            ts = 0
        if ts >= cutoff:
            out.append(r)
    return out


# ============================================================
# 购药记录
# ============================================================

async def get_medication_records(
    db: AsyncSession, user_id: str | int, limit: int = 100
) -> list[MedicationRecord]:
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(MedicationRecord)
        .where(MedicationRecord.user_id == uid)
        .order_by(desc(MedicationRecord.date))
        .limit(limit)
    )
    return list(result.scalars().all())


# ============================================================
# 授权记录
# ============================================================

async def get_active_authorizations(
    db: AsyncSession, user_id: str | int
) -> list[DataAuthorization]:
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(DataAuthorization)
        .where(DataAuthorization.user_id == uid, DataAuthorization.is_active.is_(True))
        .order_by(desc(DataAuthorization.authorized_at))
    )
    return list(result.scalars().all())


async def create_authorization(
    db: AsyncSession,
    user_id: str | int,
    data_type: str,
    authorized_agent: str,
    duration_days: int = 365,
) -> DataAuthorization:
    uid = _normalize_user_id(user_id)
    now = datetime.now(UTC)
    expires_at = datetime.fromtimestamp(now.timestamp() + duration_days * 86400, tz=UTC)
    auth = DataAuthorization(
        user_id=uid,
        data_type=data_type,
        authorized_agent=authorized_agent,
        authorized_at=now,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(auth)
    await db.commit()
    await db.refresh(auth)
    return auth


async def revoke_authorization(db: AsyncSession, auth_id: int) -> bool:
    result = await db.execute(
        select(DataAuthorization).where(DataAuthorization.id == auth_id)
    )
    auth = result.scalar_one_or_none()
    if auth is None:
        return False
    auth.is_active = False
    await db.commit()
    return True


# ============================================================
# 政策文档
# ============================================================

async def get_policy_documents(
    db: AsyncSession, category: str | None = None, limit: int = 50
) -> list[PolicyDocument]:
    stmt = select(PolicyDocument).order_by(desc(PolicyDocument.publish_date)).limit(limit)
    if category:
        stmt = stmt.where(PolicyDocument.category == category)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_policy_document(db: AsyncSession, policy_id: int) -> PolicyDocument | None:
    result = await db.execute(select(PolicyDocument).where(PolicyDocument.id == policy_id))
    return result.scalar_one_or_none()


# ============================================================
# 辅助：用户画像聚合（供 orchestrator / policy_matcher 使用）
# ============================================================

async def get_user_health_profile(db: AsyncSession, user_id: str | int) -> dict:
    """聚合用户健康画像原始数据（用药/就诊/慢病推断），供健康评分与 LLM 注入。

    返回结构化 dict，不含评分（评分由 health_engine 计算）。
    """
    uid = _normalize_user_id(user_id)
    user = await get_user(db, uid)
    if user is None:
        return {"user_id": uid, "found": False}

    meds = await get_medication_records(db, uid, limit=100)
    visits = await get_medical_records(db, uid, limit=100)

    # 慢病推断（基于购药分类）
    med_categories = {m.category for m in meds}
    chronic_diseases = []
    if any("降糖" in c or "糖尿病" in c for c in med_categories):
        chronic_diseases.append("糖尿病")
    if any("降压" in c or "高血压" in c for c in med_categories):
        chronic_diseases.append("高血压")
    if any("调脂" in c or "血脂" in c or "冠心" in c for c in med_categories):
        chronic_diseases.append("冠心病/高血脂")

    # 诊断推断（基于就诊记录）
    diagnoses = list({v.diagnosis for v in visits if v.diagnosis})[:10]

    # 档案管家：用户自述/上传资料归档（供各 Agent 上下文与政策匹配共享，原文转述、无推断）
    body_records = await get_body_records(db, uid, limit=50)

    return {
        "body_record_count": len(body_records),
        "body_organs": sorted({_organ_label(r.organ) for r in body_records}),
        "body_recent": [
            f"[{r.event_date or '日期未注明'}][{r.source_label}] {_organ_label(r.organ)}：{r.description}"
            for r in body_records[:5]
        ],
        "user_id": uid,
        "found": True,
        "name": user.name,
        "age": user.age,
        "gender": user.gender,
        "city": user.city,
        "insurance_type": user.insurance_type,
        "employee_status": user.employee_status,
        "chronic_diseases": chronic_diseases,
        "medication_categories": sorted(med_categories),
        "medications": [
            {
                "name": m.medication_name,
                "category": m.category,
                "date": m.date.isoformat() if m.date else None,
                "quantity": m.quantity,
                "unit_price": m.unit_price,
                "is_chronic": m.is_chronic,
            }
            for m in meds[:20]
        ],
        "recent_visits": len(visits),
        "visit_count_6m": len(await get_medical_records_in_range(db, uid, months=6)),
        "diagnoses": diagnoses,
        "annual_medical_cost": sum(v.total_cost or 0 for v in visits),
        "annual_medication_cost": sum((m.unit_price or 0) * (m.quantity or 0) for m in meds),
    }


# ============================================================
# 内部工具
# ============================================================

def _normalize_user_id(user_id: str | int) -> int:
    """把 'user_001' / '001' / 1 统一成 int。容错：解析失败返回 1。"""
    if isinstance(user_id, int):
        return user_id
    if user_id is None:
        return 1
    s = str(user_id).strip()
    # 提取数字部分
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return 1
    # 去掉前导零，但保证至少为 1
    n = int(digits.lstrip("0") or "0")
    return n if n > 0 else 1


# ============================================================
# EEG 脑电记录（BCI×医保创新模块）
# ============================================================

async def create_eeg_record(
    db: AsyncSession,
    user_id: str | int,
    session_id: str,
    duration_seconds: int,
    mental_state: str,
    mental_state_label: str,
    avg_band_powers: dict,
    metrics: dict,
    alert_count: int = 0,
    policy_link_count: int = 0,
    summary: str = "",
) -> EEGRecord:
    """保存一次 EEG 会话评估结果摘要。"""
    uid = _normalize_user_id(user_id)
    record = EEGRecord(
        user_id=uid,
        session_id=session_id,
        duration_seconds=duration_seconds,
        mental_state=mental_state,
        mental_state_label=mental_state_label,
        avg_band_powers=json.dumps(avg_band_powers, ensure_ascii=False),
        metrics=json.dumps(metrics, ensure_ascii=False),
        alert_count=alert_count,
        policy_link_count=policy_link_count,
        summary=summary,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_eeg_records(
    db: AsyncSession, user_id: str | int, limit: int = 20
) -> list[EEGRecord]:
    """获取用户 EEG 历史记录（按时间倒序）。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(EEGRecord)
        .where(EEGRecord.user_id == uid)
        .order_by(desc(EEGRecord.recorded_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_latest_eeg_record(
    db: AsyncSession, user_id: str | int
) -> EEGRecord | None:
    """获取用户最近一次 EEG 记录。"""
    records = await get_eeg_records(db, user_id, limit=1)
    return records[0] if records else None


def eeg_record_to_dict(record: EEGRecord) -> dict:
    """把 EEGRecord ORM 转为 dict（含反序列化的 JSON 字段）。"""
    return {
        "id": record.id,
        "user_id": record.user_id,
        "session_id": record.session_id,
        "recorded_at": record.recorded_at.isoformat() if record.recorded_at else None,
        "duration_seconds": record.duration_seconds,
        "mental_state": record.mental_state,
        "mental_state_label": record.mental_state_label,
        "avg_band_powers": json.loads(record.avg_band_powers) if record.avg_band_powers else {},
        "metrics": json.loads(record.metrics) if record.metrics else {},
        "alert_count": record.alert_count,
        "policy_link_count": record.policy_link_count,
        "summary": record.summary or "",
    }


# ============================================================
# 医学影像检查记录（MedSignal 影像引擎）
# ============================================================

async def create_imaging_record(
    db: AsyncSession,
    user_id: str | int,
    study_id: str,
    study_type: str,
    seed: int,
    findings: list | dict,
    final_findings: list | dict | None,
    report: dict | None,
    risk_level: str,
    policy_link_count: int = 0,
) -> ImagingRecord:
    """保存一次医学影像 AI 分析会话结果。"""
    uid = _normalize_user_id(user_id)
    record = ImagingRecord(
        user_id=uid,
        study_id=study_id,
        study_type=study_type,
        seed=seed,
        findings=json.dumps(findings, ensure_ascii=False),
        final_findings=json.dumps(final_findings, ensure_ascii=False) if final_findings is not None else None,
        report=json.dumps(report, ensure_ascii=False) if report is not None else None,
        risk_level=risk_level,
        policy_link_count=policy_link_count,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_imaging_records(
    db: AsyncSession, user_id: str | int, limit: int = 20
) -> list[ImagingRecord]:
    """获取用户医学影像检查历史（按时间倒序）。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(ImagingRecord)
        .where(ImagingRecord.user_id == uid)
        .order_by(desc(ImagingRecord.recorded_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_imaging_record(
    db: AsyncSession, record_id: int
) -> ImagingRecord | None:
    """根据记录 id 查询医学影像检查记录。"""
    result = await db.execute(
        select(ImagingRecord).where(ImagingRecord.id == record_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# 泛癌卫士（Oncoformer 预测存档）
# ---------------------------------------------------------------------------

async def create_cancer_prediction(
    db: AsyncSession,
    user_id: str | int,
    engine: str,
    mode: str,
    source: str,
    result: dict | list,
) -> "CancerPredictionRecord":
    """保存一次泛癌风险预测结果。"""
    from app.models import CancerPredictionRecord

    uid = _normalize_user_id(user_id)
    record = CancerPredictionRecord(
        user_id=uid,
        engine=engine,
        mode=mode,
        source=source,
        result=json.dumps(result, ensure_ascii=False),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_cancer_predictions(
    db: AsyncSession, user_id: str | int, limit: int = 20
) -> list:
    """获取用户泛癌预测历史（按时间倒序）。"""
    from app.models import CancerPredictionRecord

    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(CancerPredictionRecord)
        .where(CancerPredictionRecord.user_id == uid)
        .order_by(desc(CancerPredictionRecord.recorded_at))
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_imaging_record(
    db: AsyncSession,
    record: ImagingRecord,
    final_findings: list | dict | None = None,
    report: dict | None = None,
    risk_level: str | None = None,
    policy_link_count: int | None = None,
) -> ImagingRecord:
    """更新影像记录（医生复核后覆盖最终标注/报告）。"""
    if final_findings is not None:
        record.final_findings = json.dumps(final_findings, ensure_ascii=False)
    if report is not None:
        record.report = json.dumps(report, ensure_ascii=False)
    if risk_level is not None:
        record.risk_level = risk_level
    if policy_link_count is not None:
        record.policy_link_count = policy_link_count
    await db.commit()
    await db.refresh(record)
    return record


def imaging_record_to_dict(record: ImagingRecord) -> dict:
    """把 ImagingRecord ORM 转为 dict（含反序列化的 JSON 字段）。"""
    return {
        "id": record.id,
        "user_id": record.user_id,
        "study_id": record.study_id,
        "study_type": record.study_type,
        "seed": record.seed,
        "recorded_at": record.recorded_at.isoformat() if record.recorded_at else None,
        "findings": json.loads(record.findings) if record.findings else [],
        "final_findings": json.loads(record.final_findings) if record.final_findings else None,
        "report": json.loads(record.report) if record.report else None,
        "risk_level": record.risk_level,
        "policy_link_count": record.policy_link_count,
    }


# ============================================================
# 人体健康档案（档案管家）— 只增不删
# ============================================================

async def create_body_document(
    db: AsyncSession,
    user_id: str | int,
    filename: str,
    mime_type: str,
    doc_kind: str,
    extracted_text: str,
) -> BodyDocument:
    """存档一份上传资料（只存解析文本）。

    OCR 失败导致文本为空时，若同名资料已有非空存档，继承其文本与类型，
    避免转录服务抖动把档案降级为空内容。
    """
    uid = _normalize_user_id(user_id)
    text = extracted_text or ""
    if not text.strip():
        stmt = (
            select(BodyDocument)
            .where(BodyDocument.user_id == uid, BodyDocument.filename == filename[:200])
            .order_by(desc(BodyDocument.uploaded_at), desc(BodyDocument.id))
            .limit(5)
        )
        for prev in (await db.execute(stmt)).scalars().all():
            if (prev.extracted_text or "").strip():
                text = prev.extracted_text
                if doc_kind == "其他" and prev.doc_kind:
                    doc_kind = prev.doc_kind
                logger.warning("本次转录为空，继承同名历史存档文本: %s", filename)
                break
    doc = BodyDocument(
        user_id=uid,
        filename=filename[:200],
        mime_type=mime_type[:80],
        doc_kind=doc_kind,
        extracted_text=text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def create_body_records(
    db: AsyncSession,
    user_id: str | int,
    records: list[dict],
    *,
    source_type: str,
    source_label: str,
    source_ref: str = "",
    document_id: int | None = None,
    batch_id: str = "",
) -> list[BodyRecord]:
    """追加一批档案记录（同一 batch_id = 同一次归档周期）。永不覆盖旧记录。"""
    uid = _normalize_user_id(user_id)
    rows = [
        BodyRecord(
            user_id=uid,
            organ=r["organ"],
            description=r.get("description") or r.get("raw_excerpt") or "",
            raw_excerpt=r.get("raw_excerpt") or "",
            event_date=(r.get("event_date") or "")[:10],
            source_type=source_type,
            source_label=source_label,
            source_ref=(source_ref or "")[:200],
            document_id=document_id,
            batch_id=batch_id,
        )
        for r in records
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def get_body_records(
    db: AsyncSession, user_id: str | int, organ: str | None = None, limit: int = 500
) -> list[BodyRecord]:
    """按时间倒序（检查时间优先，其次归档时间）返回档案记录；未注明时间的排最后。"""
    uid = _normalize_user_id(user_id)
    stmt = select(BodyRecord).where(BodyRecord.user_id == uid)
    if organ:
        stmt = stmt.where(BodyRecord.organ == organ)
    stmt = stmt.order_by(desc(BodyRecord.event_date), desc(BodyRecord.created_at), desc(BodyRecord.id)).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_body_documents(
    db: AsyncSession, user_id: str | int, limit: int = 100
) -> list[BodyDocument]:
    """用户已存档的资料元数据（新→旧），供 3D 查看器「患者资料」面板展示。"""
    uid = _normalize_user_id(user_id)
    result = await db.execute(
        select(BodyDocument)
        .where(BodyDocument.user_id == uid)
        .order_by(desc(BodyDocument.id))
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_recent_body_documents(
    db: AsyncSession, user_id: str | int, limit: int = 5, within_minutes: int = 120
) -> list[BodyDocument]:
    """用户最近上传的医疗资料存档（按上传时间倒序），用于对话上下文感知与报销联合预审。

    同一 filename 重复上传时只保留最新一条存档，避免预审金额重复累计。
    """
    uid = _normalize_user_id(user_id)
    cutoff = datetime.now(UTC) - timedelta(minutes=within_minutes)
    stmt = (
        select(BodyDocument)
        .where(BodyDocument.user_id == uid, BodyDocument.uploaded_at >= cutoff)
        .order_by(desc(BodyDocument.uploaded_at), desc(BodyDocument.id))
        .limit(max(limit * 3, 30))
    )
    result = await db.execute(stmt)
    latest: dict[str, BodyDocument] = {}
    with_content: dict[str, BodyDocument] = {}
    for doc in result.scalars().all():  # 已按时间倒序，首次命中即最新
        latest.setdefault(doc.filename, doc)
        if (doc.extracted_text or "").strip():
            with_content.setdefault(doc.filename, doc)
    # 优先取“最新且有内容”的存档，避免转录失败的空行覆盖有效识别结果
    picked = {**latest, **with_content}
    unique = sorted(picked.values(), key=lambda d: (d.uploaded_at, d.id), reverse=True)
    return unique[:limit]


async def get_body_organ_summary(db: AsyncSession, user_id: str | int) -> dict:
    """各器官记录数与最近检查时间：{organ: {label, count, latest_event_date}}。"""
    summary: dict[str, dict] = {}
    for r in await get_body_records(db, user_id):
        s = summary.setdefault(r.organ, {"label": _organ_label(r.organ), "count": 0, "latest_event_date": ""})
        s["count"] += 1
        if r.event_date and r.event_date > s["latest_event_date"]:
            s["latest_event_date"] = r.event_date
    return summary


def body_record_to_dict(r: BodyRecord) -> dict:
    return {
        "id": r.id,
        "organ": r.organ,
        "organ_label": _organ_label(r.organ),
        "description": r.description,
        "raw_excerpt": r.raw_excerpt or "",
        "event_date": r.event_date or "",
        "source_type": r.source_type,
        "source_label": r.source_label,
        "source_ref": r.source_ref or "",
        "document_id": r.document_id,
        "batch_id": r.batch_id or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
