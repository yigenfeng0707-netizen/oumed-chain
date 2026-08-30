"""档案管家 — 信息抽取工具（纯整理，不诊断）

三级降级与项目惯例一致：
- 文本抽取：LLM（共享 orchestrator._llm）→ 关键词规则
- 图片转录：阿里云 DashScope 视觉模型（qwen-vl）→ OCR.space 原文 → 空串
- PDF：pypdf 文本层 → 空串（扫描版 PDF 无文本层，由智能体提示用户改传图片）

所有函数只摘录用户原文中**明确陈述**的内容；LLM 结果会做"原文包含性"校验，
防止模型补充原文没有的信息。
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import crud
from app.services.body import taxonomy as tx
from app.services.body.taxonomy import has_health_signal  # noqa: F401  (re-export)

logger = logging.getLogger(__name__)

SOURCE_CHAT = "对话输入"
DISCLAIMER = (
    "以上内容仅整理展示您在对话或上传资料中提供的信息，非诊断结论；"
    "本模块不是临床诊断工具，不自动检测或推断疾病。"
)

_DATE_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?"
    r"|(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?"
)
_SENT_SPLIT = re.compile(r"[。！？；\n!?;]+")
# 提到他人的句子不归档（档案只记用户本人）。# ponytail: 词表启发式；LLM 路径由提示词规则 7 兜底
_THIRD_PARTY = ("父亲", "母亲", "爸", "妈", "儿子", "女儿", "老公", "老婆", "丈夫", "妻子",
                "爷爷", "奶奶", "外公", "外婆", "孩子", "朋友", "同事", "邻居", "亲戚")

_EXTRACT_SYSTEM = """你是 MedSignal 档案管家的信息整理模块，不是医生。
任务：从用户原文中摘录**已明确陈述**的身体部位/症状/检查结果，按器官归档。
规则：
1. 只摘录原文已有信息，不推断、不补充诊断、不加评价。
2. organ 只能取以下 key：{keys}
3. raw_excerpt 必须是原文中的连续片段（逐字复制）。
4. description 用一句话转述 raw_excerpt，不得加入原文没有的内容。
5. event_date 取原文中该信息对应的时间，格式 YYYY-MM 或 YYYY-MM-DD；原文未说明则为空字符串。
6. 成对部位（肩/臂/膝/腿）只有原文写明左右才用 *_left/*_right，否则用通用 key。
7. 只归档用户本人的情况；涉及他人（父母、子女、配偶、朋友等）的描述一律不归档。
只返回 JSON 数组，不要其他内容：
[{{"organ":"lungs","description":"...","raw_excerpt":"...","event_date":"2026-02"}}]
原文没有任何身体部位相关的健康信息时返回 []。"""


# ------------------------------------------------------------------
# 文本抽取
# ------------------------------------------------------------------

def parse_event_date(text: str) -> str:
    """提取第一处日期 → 'YYYY-MM' 或 'YYYY-MM-DD'；无则空串。"""
    m = _DATE_RE.search(text)
    if not m:
        return ""
    y, mo, d = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(4), m.group(5), m.group(6))
    if not 1 <= int(mo) <= 12:
        return ""
    out = f"{y}-{int(mo):02d}"
    if d and 1 <= int(d) <= 31:
        out += f"-{int(d):02d}"
    return out


def rule_extract(text: str) -> list[dict]:
    """关键词规则抽取：逐句匹配器官别名，日期向后沿用（报告日期常在开头）。

    # ponytail: keyword rules; LLM path takes over when a key is configured
    """
    records: list[dict] = []
    last_date = ""
    for sent in _SENT_SPLIT.split(text):
        s = sent.strip()
        if not s or any(w in s for w in _THIRD_PARTY):
            continue
        date = parse_event_date(s) or last_date
        if parse_event_date(s):
            last_date = date
        for organ in tx.match_organs(s):
            records.append({"organ": organ, "description": s, "raw_excerpt": s, "event_date": date})
    return records


def _contained(excerpt: str, source: str) -> bool:
    """原文包含性校验：逐字包含，或最长公共子串 ≥ 60%。"""
    e = re.sub(r"\s+", "", excerpt)
    s = re.sub(r"\s+", "", source)
    if not e:
        return False
    if e in s:
        return True
    match = difflib.SequenceMatcher(None, e, s).find_longest_match(0, len(e), 0, len(s))
    return match.size / len(e) >= 0.6


def _validate(items: Any, source: str) -> list[dict] | None:
    if isinstance(items, dict):
        items = items.get("records") or items.get("items") or items.get("data")
    if not isinstance(items, list):
        return None
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        organ = str(it.get("organ", "")).strip()
        excerpt = str(it.get("raw_excerpt", "")).strip()
        if organ not in tx.ORGANS or not _contained(excerpt, source):
            logger.info("丢弃抽取项（器官未知或原文不包含）: %s", it)
            continue
        out.append({
            "organ": organ,
            "description": str(it.get("description") or excerpt).strip(),
            "raw_excerpt": excerpt,
            "event_date": parse_event_date(str(it.get("event_date", ""))) or "",
        })
    return out


async def extract_from_text(text: str, llm=None) -> list[dict]:
    """LLM 抽取（带原文校验）→ 失败降级规则抽取。"""
    if llm is not None:
        try:
            from app.services.llm_service import LLMService

            resp = await llm.chat(
                messages=[
                    {"role": "system", "content": _EXTRACT_SYSTEM.format(keys=", ".join(tx.ORGANS))},
                    {"role": "user", "content": text[:4000]},
                ],
                temperature=0.1,
            )
            validated = _validate(LLMService._parse_json_response(resp), text)
            if validated is not None:
                return validated
            logger.warning("LLM 抽取结果无法解析，降级规则抽取")
        except Exception as e:
            logger.warning("LLM 抽取失败: %s，降级规则抽取", e)
    return rule_extract(text)


def missing_info(records: list[dict]) -> list[str]:
    """记录缺少的关键信息 → 澄清问题（不猜测）。"""
    questions: list[str] = []
    for r in records:
        label = tx.label_of(r["organ"])
        if r["organ"] in tx.PAIRED:
            questions.append(f"您提到的{label}情况是左侧还是右侧？")
        if not r.get("event_date"):
            questions.append(f"{label}相关情况的检查或发生时间大概是什么时候？")
    return list(dict.fromkeys(questions))


# ------------------------------------------------------------------
# 资料转录（图片 / PDF / 文本）
# ------------------------------------------------------------------

async def extract_from_image(data: bytes, mime: str, llm=None, filename: str = "image.jpg") -> str:
    """图片 → 文字：DashScope 视觉模型逐字转录 → OCR.space 原文 → 空串。"""
    if llm is not None:
        try:
            b64 = base64.b64encode(data).decode()
            text = await llm.chat_vision([
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime or 'image/jpeg'};base64,{b64}"}},
                        {"type": "text", "text": "请逐字转录这张医学报告/病历图片中的全部文字，保持原文，不要总结、不要解读。"},
                    ],
                }
            ])
            if text:
                return text
        except Exception as e:
            logger.warning("视觉模型转录失败: %s，降级 OCR", e)
    try:
        from app.services.ocr_service import get_ocr_service

        return await get_ocr_service().recognize_text(data, filename)
    except Exception as e:
        logger.warning("OCR 转录失败: %s", e)
        return ""


def extract_from_pdf(data: bytes) -> str:
    """PDF 文本层提取。# ponytail: 扫描版 PDF 无文本层 → 返回空串，由智能体提示改传图片"""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception as e:
        logger.warning("PDF 解析失败: %s", e)
        return ""


def classify_doc_kind(filename: str, text: str) -> str:
    head = f"{filename} {text[:500]}".upper()
    if any(k in head for k in ("MRI", "磁共振", "核磁")):
        return "MRI报告"
    if "CT" in head:
        return "CT报告"
    return "病历文本" if text.strip() else "其他"


# ------------------------------------------------------------------
# 归档入口
# ------------------------------------------------------------------

async def ingest_text(
    db: AsyncSession,
    user_id: str | int,
    text: str,
    *,
    source_type: str,
    source_label: str,
    source_ref: str = "",
    llm=None,
    document_id: int | None = None,
) -> list[dict]:
    """门控 → 抽取 → 追加入库，返回新增记录 dict 列表（一个 batch_id = 一次归档周期）。"""
    if not text or not tx.has_health_signal(text):
        return []
    records = await extract_from_text(text, llm)
    if not records:
        return []
    rows = await crud.create_body_records(
        db,
        user_id,
        records,
        source_type=source_type,
        source_label=source_label,
        source_ref=source_ref,
        document_id=document_id,
        batch_id=uuid.uuid4().hex[:12],
    )
    return [crud.body_record_to_dict(r) for r in rows]


def records_to_text(records: list[dict]) -> str:
    """把记录逐条渲染为 [日期][来源] 器官：转述（供结构化回答与 LLM 上下文）。"""
    return "\n".join(
        f"- [{r.get('event_date') or '日期未注明'}][{r.get('source_label', '')}] "
        f"{r.get('organ_label') or tx.label_of(r.get('organ', ''))}：{r.get('description', '')}"
        for r in records
    )


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
