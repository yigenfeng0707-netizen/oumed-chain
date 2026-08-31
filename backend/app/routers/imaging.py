"""
瓯医数链 Agent - 医学影像 AI 标注路由

提供影像检查类型查询、AI 影像分析、医生复核标注、结构化报告、
影像-医保联动推荐等 API。前端影像标注工作台（/imaging）对接本模块。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.database import async_session
from app.services.imaging import (
    FINDINGS_META,
    STUDY_TYPES,
    apply_doctor_review,
    build_report,
    generate_study,
    link_to_imaging_policies,
)
from app.services.vision_service import get_vision_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/imaging", tags=["imaging"])


# ============================================================
# 辅助
# ============================================================

_SEVERITY_LABEL = {"low": "低危", "medium": "中危", "high": "高危"}


def _summarize_findings(findings: list) -> str:
    """将 Finding 列表压成一句话摘要（供视觉模型解读参考）。"""
    if not findings:
        return ""
    parts = []
    for f in findings:
        label = FINDINGS_META.get(f.finding_type, {}).get("label", f.finding_type)
        sev = _SEVERITY_LABEL.get(getattr(f, "severity", "medium"), "中危")
        conf = getattr(f, "confidence", 0.8)
        parts.append(f"{label}（{sev}，置信度{conf:.0%}）")
    return "；".join(parts)


async def _vision_interpretation(image_base64: str, study_label: str, findings_summary: str):
    """调用视觉模型生成影像所见解读（不可用时返回 None，不影响主流程）。

    同步 SDK 用 asyncio.to_thread 包装，避免阻塞事件循环。
    """
    try:
        vs = get_vision_service()
        if vs is None or not image_base64:
            return None
        return await asyncio.to_thread(
            vs.interpret_imaging_study, image_base64, study_label, findings_summary
        )
    except Exception as e:
        logger.warning("视觉模型解读异常（已降级跳过）: %s", e)
        return None


# ============================================================
# Pydantic 请求模型
# ============================================================

class AnalyzeRequest(BaseModel):
    """发起一次影像 AI 分析。"""
    study_type: str = Field(..., description="检查类型：chest_xray / lung_ct / brain_mri")
    findings_keys: list[str] | None = Field(
        default=None, description="植入病灶类别；缺省时使用该类型的全部类别"
    )
    seed: int | None = Field(default=None, description="确定性种子，缺省时自动生成")
    with_vision: bool = Field(
        default=False,
        description="是否调用视觉大模型（GLM-4.6V）生成自然语言影像解读；"
                    "未配置 Key 或调用失败时自动降级跳过",
    )


class DoctorAnnotation(BaseModel):
    """医生复核标注操作。"""
    action: str = Field(..., description="confirm / reject / add / update")
    index: int | None = Field(default=None, description="AI 发现索引（confirm/reject 用）")
    finding_type: str = Field(default="nodule", description="病灶类别")
    x: float = Field(default=0.5, ge=0, le=1, description="归一化中心 x")
    y: float = Field(default=0.5, ge=0, le=1, description="归一化中心 y")
    w: float = Field(default=0.06, ge=0.01, le=1, description="归一化宽")
    h: float = Field(default=0.06, ge=0.01, le=1, description="归一化高")
    confidence: float = Field(default=0.9, ge=0, le=1, description="置信度")
    severity: str = Field(default="medium", description="严重度：low/medium/high")
    evidence: str = Field(default="医师人工复核标注", description="标注证据")


class DoctorReviewRequest(BaseModel):
    """医生复核请求：对 AI 预标注做确认/驳回/修正/新增。"""
    annotations: list[DoctorAnnotation] = Field(default_factory=list)


# ============================================================
# 接口
# ============================================================

@router.get("/study-types")
async def list_study_types():
    """支持的检查类型与病灶类别（前端标注工作台配置）。"""
    return {
        "study_types": {
            k: {
                "label": v["label"],
                "short_label": v["short_label"],
                "findings": [
                    {
                        "key": fk,
                        "label": FINDINGS_META[fk]["label"],
                        "severity": FINDINGS_META[fk]["severity"],
                        "desc": FINDINGS_META[fk]["desc"],
                    }
                    for fk in v["findings"]
                    if fk in FINDINGS_META
                ],
            }
            for k, v in STUDY_TYPES.items()
        }
    }


@router.post("/{user_id}/analyze")
async def analyze_image(user_id: str, req: AnalyzeRequest):
    """AI 影像分析：生成合成影像 → 病灶检测 → AI 预标注 → 结构化报告。

    现场路演演示：选择检查类型 → 一键生成影像 → AI 自动框出病灶并给出
    类别/置信度/严重度 → 医生可确认或修正。
    """
    if req.study_type not in STUDY_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的检查类型: {req.study_type}，可选 {list(STUDY_TYPES)}",
        )

    study = generate_study(
        study_type=req.study_type,
        findings_keys=req.findings_keys,
        seed=req.seed,
    )

    # 医保联动
    policy_links = link_to_imaging_policies(study.findings)

    # 视觉大模型影像解读（可选，降级不影响主流程）
    vision_interpretation = None
    if req.with_vision:
        vision_interpretation = await _vision_interpretation(
            study.image_base64,
            STUDY_TYPES[study.study_type]["label"],
            _summarize_findings(study.findings),
        )

    async with async_session() as db:
        from app import crud
        record = await crud.create_imaging_record(
            db=db,
            user_id=user_id,
            study_id=study.study_id,
            study_type=study.study_type,
            seed=study.seed,
            findings=[f.to_dict() for f in study.findings],
            final_findings=None,
            report=study.report,
            risk_level=study.report.get("risk_level", "待复核"),
            policy_link_count=len(policy_links),
        )

    return {
        "record_id": record.id,
        "study_id": study.study_id,
        "study_type": study.study_type,
        "study_label": STUDY_TYPES[study.study_type]["label"],
        "seed": study.seed,
        "image_base64": study.image_base64,
        "findings": [f.to_dict() for f in study.findings],
        "report": study.report,
        "policy_links": policy_links,
        "vision_interpretation": vision_interpretation,
        "vision_available": vision_interpretation is not None,
        "disclaimer": "本结果由 AI 辅助生成，仅供筛查参考，最终诊断须由持证医师复核确认。",
    }


@router.post("/{user_id}/records/{record_id}/review")
async def doctor_review(user_id: str, record_id: int, req: DoctorReviewRequest):
    """医生复核：确认/驳回/修正 AI 标注，生成最终报告。

    前端工作台演示：AI 预标注 → 医生逐框确认/驳回/修正 → 提交 →
    返回最终结构化报告与医保联动建议。
    """
    async with async_session() as db:
        from app import crud
        record = await crud.get_imaging_record(db, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="影像记录不存在")

        # 反序列化 AI 发现，构造 Finding 对象
        from app.services.imaging import Finding
        raw_findings = json.loads(record.findings) if record.findings else []
        ai_findings = [
            Finding(
                finding_type=f["finding_type"],
                x=f["x"], y=f["y"], w=f["w"], h=f["h"],
                confidence=f.get("confidence", 0.8),
                severity=f.get("severity", "medium"),
                source="ai",
                status=f.get("status", "pending"),
                evidence=f.get("evidence", ""),
            )
            for f in raw_findings
        ]

        # 应用医生标注
        ops = [a.dict() for a in req.annotations]
        final_findings = apply_doctor_review(ai_findings, ops)

        # 生成最终报告
        report = build_report(final_findings)
        policy_links = link_to_imaging_policies(final_findings)

        await crud.update_imaging_record(
            db=db,
            record=record,
            final_findings=[f.to_dict() for f in final_findings],
            report=report,
            risk_level=report.get("risk_level", "待复核"),
            policy_link_count=len(policy_links),
        )

    return {
        "record_id": record_id,
        "final_findings": [f.to_dict() for f in final_findings],
        "report": report,
        "policy_links": policy_links,
    }


@router.get("/{user_id}/records")
async def list_records(user_id: str, limit: int = Query(10, ge=1, le=50)):
    """用户医学影像检查历史。"""
    async with async_session() as db:
        from app import crud
        records = await crud.get_imaging_records(db, user_id, limit=limit)
        return {
            "records": [
                {
                    **crud.imaging_record_to_dict(r),
                    "study_label": STUDY_TYPES.get(r.study_type, {}).get("label", r.study_type),
                }
                for r in records
            ]
        }


@router.get("/{user_id}/records/{record_id}")
async def get_record(user_id: str, record_id: int):
    """单条影像记录详情（含可复现影像）。"""
    async with async_session() as db:
        from app import crud
        record = await crud.get_imaging_record(db, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="影像记录不存在")

        # 由确定性参数复现影像
        from app.services.imaging import Finding, render_study_image
        raw_findings = json.loads(record.findings) if record.findings else []
        findings = [
            Finding(
                finding_type=f["finding_type"],
                x=f["x"], y=f["y"], w=f["w"], h=f["h"],
                confidence=f.get("confidence", 0.8),
                severity=f.get("severity", "medium"),
                source=f.get("source", "ai"),
                status=f.get("status", "pending"),
                evidence=f.get("evidence", ""),
            )
            for f in raw_findings
        ]
        image_b64 = render_study_image(record.study_type, findings, record.seed)

        return {
            **crud.imaging_record_to_dict(record),
            "study_label": STUDY_TYPES.get(record.study_type, {}).get("label", record.study_type),
            "image_base64": image_b64,
        }


@router.get("/{user_id}/policy-links/{record_id}")
async def get_policy_links(user_id: str, record_id: int):
    """影像-医保联动推荐（基于最终标注）。"""
    async with async_session() as db:
        from app import crud
        record = await crud.get_imaging_record(db, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="影像记录不存在")
        raw_final = json.loads(record.final_findings) if record.final_findings else None
        raw_findings = json.loads(record.findings) if record.findings else []
        final_findings = raw_final if raw_final is not None else raw_findings

        from app.services.imaging import Finding
        f_list = [
            Finding(
                finding_type=f["finding_type"],
                x=f["x"], y=f["y"], w=f["w"], h=f["h"],
                confidence=f.get("confidence", 0.8),
                severity=f.get("severity", "medium"),
                source=f.get("source", "ai"),
                status=f.get("status", "pending"),
                evidence=f.get("evidence", ""),
            )
            for f in final_findings
        ]
        return {"policy_links": link_to_imaging_policies(f_list)}


# ============================================================
# 真实公开数据集影像（scripts/ingest_real_imaging.py 产出的 manifest）
# ============================================================

# 项目根目录：backend/app/routers/imaging.py -> 上溯 3 级到项目根
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REAL_IMAGING_DIR = _PROJECT_ROOT / "data" / "real_imaging"
_REAL_MANIFEST = _REAL_IMAGING_DIR / "manifest.json"


def _load_real_manifest() -> dict:
    """读取真实影像数据集 manifest（不存在时返回空结构，不报错）。"""
    try:
        return json.loads(_REAL_MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": "1.0", "datasets": {}, "studies": []}
    except Exception as e:
        logger.warning("读取真实影像 manifest 失败: %s", e)
        return {"version": "1.0", "datasets": {}, "studies": []}


@router.get("/real/list")
async def list_real_studies(
    study_type: str | None = Query(None, description="按检查类型过滤"),
    source: str | None = Query(None, description="按数据源过滤"),
    limit: int = Query(20, ge=1, le=100),
):
    """真实公开数据集影像列表（Montgomery/Shenzhen/本地导入等）。

    数据由 scripts/ingest_real_imaging.py 接入，manifest 位于
    data/real_imaging/manifest.json。返回概览（不含大体积 base64）。
    """
    m = _load_real_manifest()
    studies = m.get("studies", [])
    if study_type:
        studies = [s for s in studies if s.get("study_type") == study_type]
    if source:
        studies = [s for s in studies if s.get("source") == source]
    studies = studies[:limit]
    return {
        "total": len(m.get("studies", [])),
        "returned": len(studies),
        "datasets": m.get("datasets", {}),
        "studies": [
            {
                "study_id": s["study_id"],
                "study_type": s["study_type"],
                "study_label": s.get("study_label", s["study_type"]),
                "source": s.get("source"),
                "origin_file": s.get("origin_file"),
                "detected_count": len(s.get("detected_findings", [])),
                "gt_count": len(s.get("gt_findings") or []),
                "metrics": s.get("metrics"),
            }
            for s in studies
        ],
        "note": "真实公开数据集影像（脱敏科研用途），AI 检测结果仅供辅助参考，最终诊断须由持证医师复核。",
    }


@router.get("/real/{study_id}")
async def get_real_study(study_id: str, with_vision: bool = Query(False, description="是否生成视觉大模型影像解读")):
    """单条真实数据集影像详情：影像 base64 + AI 检测 + GT 标注 + 评估指标 + 医保联动。"""
    m = _load_real_manifest()
    target = None
    for s in m.get("studies", []):
        if s.get("study_id") == study_id:
            target = s
            break
    if target is None:
        raise HTTPException(status_code=404, detail=f"真实影像研究不存在: {study_id}")

    # 读取标准化影像文件 -> data URI
    image_b64 = ""
    img_path = _PROJECT_ROOT / target.get("image_path", "")
    try:
        image_b64 = "data:image/png;base64," + base64.b64encode(img_path.read_bytes()).decode("ascii")
    except Exception as e:
        logger.warning("读取真实影像文件失败 %s: %s", img_path, e)

    # 医保联动（基于 AI 检测结果）
    from app.services.imaging import Finding
    det = target.get("detected_findings", [])
    f_list = [
        Finding(
            finding_type=f["finding_type"],
            x=f["x"], y=f["y"], w=f["w"], h=f["h"],
            confidence=f.get("confidence", 0.8),
            severity=f.get("severity", "medium"),
            source="ai", status="pending",
            evidence=f.get("evidence", ""),
        )
        for f in det
    ]
    policy_links = link_to_imaging_policies(f_list)

    # 视觉大模型解读（可选）：真实公开数据集胸片 + GLM-4.6V 多模态解读
    vision_interpretation = None
    if with_vision and image_b64:
        summary_parts = []
        for f in det:
            label = FINDINGS_META.get(f.get("finding_type", ""), {}).get("label", f.get("finding_type", "未知"))
            sev = _SEVERITY_LABEL.get(f.get("severity", "medium"), "中危")
            summary_parts.append(f"{label}（{sev}，置信度{f.get('confidence', 0.8):.0%}）")
        vision_interpretation = await _vision_interpretation(
            image_b64,
            target.get("study_label", target["study_type"]),
            "；".join(summary_parts),
        )

    return {
        "study_id": target["study_id"],
        "study_type": target["study_type"],
        "study_label": target.get("study_label", target["study_type"]),
        "source": target.get("source"),
        "origin_file": target.get("origin_file"),
        "origin_shape": target.get("origin_shape"),
        "image_base64": image_b64,
        "detected_findings": det,
        "gt_findings": target.get("gt_findings"),
        "metrics": target.get("metrics"),
        "policy_links": policy_links,
        "vision_interpretation": vision_interpretation,
        "vision_available": vision_interpretation is not None,
        "disclaimer": "真实公开数据集影像（脱敏科研用途）。AI 检测仅供辅助参考，最终诊断须由持证医师复核。",
    }
