"""
MedSignal - 药品卫士引擎（药品拍照识别 × 用药安全）

识别管线：qwen-vl 视觉抽取 → OCR 文本 + LLM 解析 → mock 兜底
富化层：药品归类（drug_categories）/ 相互作用核查（interactions）/ 有效期核验
登记：仅当用户明确确认后调用 register_drug 写入 medication_records

设计原则与项目一致：任何一环失败都降级，Demo 永不翻车。
"""

import base64
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# repo 根目录下的 data/（engine.py 上溯 4 层：drug_scan → services → app → backend → repo）
_DATA_DIR = Path(__file__).resolve().parents[4] / "data"

DRUG_FIELDS = [
    "generic_name", "brand_name", "spec", "dosage_form", "manufacturer",
    "approval_number", "batch_number", "production_date", "expiry_date", "otc_or_rx",
]

# 国药准字 + 1 位字母 + 8 位数字
_APPROVAL_RE = re.compile(r"^国药准字[A-Z]\d{8}$")

_EXPIRING_SOON_MONTHS = 3


# ============================================================
# 规则库
# ============================================================

def load_drug_rules() -> dict:
    """加载 drug_interaction_rules.json，失败返回空 dict。"""
    try:
        return json.loads((_DATA_DIR / "drug_interaction_rules.json").read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("加载药品相互作用规则失败: %s", e)
        return {}


# ============================================================
# 识别结果规范化
# ============================================================

def empty_drug() -> dict:
    drug = dict.fromkeys(DRUG_FIELDS, "")
    drug["confidence"] = 0.0
    drug["notes"] = ""
    return drug


def normalize_drug_fields(raw: dict) -> dict:
    """清洗/校验识别结果：未知字段过滤、占位符清空、批准文号格式校验。"""
    drug = empty_drug()
    for f in DRUG_FIELDS:
        v = str(raw.get(f) or "").strip()
        if v.lower() in ("null", "none", "n/a", "-", "无", "未识别"):
            v = ""
        drug[f] = v

    # 批准文号格式硬校验：不符合即留空并在 notes 说明
    if drug["approval_number"]:
        cleaned = drug["approval_number"].replace(" ", "")
        if _APPROVAL_RE.match(cleaned):
            drug["approval_number"] = cleaned
        else:
            drug["notes"] = f"批准文号格式不符（{drug['approval_number']}），已留空"
            drug["approval_number"] = ""

    try:
        drug["confidence"] = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        drug["confidence"] = 0.0

    notes = str(raw.get("notes") or "").strip()
    if notes:
        drug["notes"] = f"{drug['notes']}；{notes}".lstrip("；") if drug["notes"] else notes
    return drug


def parse_drug_json(text: str) -> dict | None:
    """从 LLM 输出中解析 JSON（兼容 ```json 围栏与前后缀文字）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text.strip()
    if not candidate.startswith("{"):
        i = candidate.find("{")
        candidate = candidate[i:] if i >= 0 else ""
    # 去掉 JSON 之后的多余尾巴文字（取到最后一个 }）
    if candidate:
        j = candidate.rfind("}")
        candidate = candidate[: j + 1] if j >= 0 else ""
    if not candidate:
        return None
    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


# ============================================================
# 识别管线
# ============================================================

DRUG_SCAN_VISION_PROMPT = """请识别这张药品包装/标签照片，只输出图片中真实可见的信息，严禁猜测。
输出严格 JSON（不要其他内容）：
{
  "generic_name": "通用名称",
  "brand_name": "商品名",
  "spec": "规格",
  "dosage_form": "剂型",
  "manufacturer": "生产企业",
  "approval_number": "国药准字+1位字母+8位数字",
  "batch_number": "生产批号",
  "production_date": "YYYY-MM",
  "expiry_date": "有效期至 YYYY-MM",
  "otc_or_rx": "OTC/RX",
  "confidence": 0.0,
  "notes": "识别说明"
}
不可见的字段留空串。若图片不是药品包装，返回 {"not_a_drug": true, "detected": "实际内容描述"}。"""

_OCR_PARSE_PROMPT = """以下是药品包装照片的 OCR 识别文本：

{ocr_text}

请从中提取药品结构化信息，只输出如下 JSON（不要其他内容），文本中没有的字段留空串：
{{"generic_name": "", "brand_name": "", "spec": "", "dosage_form": "", "manufacturer": "",
  "approval_number": "", "batch_number": "", "production_date": "", "expiry_date": "",
  "otc_or_rx": "", "confidence": 0.0, "notes": ""}}"""

MOCK_DRUG_RESULT = {
    "generic_name": "二甲双胍片",
    "brand_name": "格华止",
    "spec": "0.5g×20片",
    "dosage_form": "片剂",
    "manufacturer": "默克制药（示例）",
    "approval_number": "国药准字H20023370",
    "batch_number": "B20250312",
    "production_date": "2025-03",
    "expiry_date": "2027-03",
    "otc_or_rx": "RX",
    "confidence": 0.92,
    "notes": "演示示例数据（识别服务不可用时降级）",
}


def mock_drug_result() -> dict:
    return dict(MOCK_DRUG_RESULT)


async def recognize_drug(image_bytes: bytes, filename: str = "drug.jpg", llm=None) -> tuple[dict, str]:
    """识别药品图片。

    Returns:
        (drug_dict, source)：source ∈ {"vision", "ocr_llm", "mock"}。
        非药品图片返回 ({"not_a_drug": True, "detected": ...}, source)。
    """
    # 1) 视觉模型优先（药盒小字/曲面，VL 明显优于纯文本 OCR）
    if llm is not None:
        try:
            b64 = base64.b64encode(image_bytes).decode()
            text = await llm.chat_vision([
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": DRUG_SCAN_VISION_PROMPT},
                    ],
                }
            ], temperature=0.1)
            data = parse_drug_json(text)
            if data:
                if data.get("not_a_drug"):
                    return {"not_a_drug": True, "detected": str(data.get("detected", ""))}, "vision"
                if str(data.get("generic_name", "")).strip():
                    return normalize_drug_fields(data), "vision"
            logger.warning("视觉模型未返回有效药品信息，降级 OCR")
        except Exception as e:
            logger.warning("药品视觉识别失败，降级 OCR: %s", e)

    # 2) OCR 文本 + LLM 解析
    if llm is not None:
        try:
            from app.services.ocr_service import get_ocr_service

            ocr_text = await get_ocr_service().recognize_text(image_bytes, filename)
            if ocr_text.strip():
                resp = await llm.chat(
                    [{"role": "user", "content": _OCR_PARSE_PROMPT.format(ocr_text=ocr_text[:2000])}],
                    temperature=0.1,
                )
                data = parse_drug_json(resp)
                if data and str(data.get("generic_name", "")).strip():
                    return normalize_drug_fields(data), "ocr_llm"
            logger.warning("OCR 文本为空或解析失败，降级 mock")
        except Exception as e:
            logger.warning("OCR+LLM 药品识别失败，降级 mock: %s", e)

    # 3) mock 兜底
    return mock_drug_result(), "mock"


# ============================================================
# 富化层
# ============================================================

def categorize_drug(generic_name: str, rules: dict | None = None) -> str:
    """按 drug_categories 归类；未命中返回空串。"""
    if not generic_name:
        return ""
    rules = rules if rules is not None else load_drug_rules()
    for cat, names in (rules.get("drug_categories") or {}).items():
        if cat.startswith("_"):
            continue
        for n in names:
            if n and (n in generic_name or generic_name in n):
                return cat
    return ""


def check_expiry(expiry_date: str) -> dict:
    """有效期核验：支持 YYYY / YYYY-MM / YYYY-MM-DD。"""
    if not expiry_date:
        return {"status": "unknown", "message": "包装上未可见有效期，请注意核对"}
    m = re.match(r"^(\d{4})(?:[-/年](\d{1,2}))?(?:[-/月](\d{1,2}))?", expiry_date.strip())
    if not m:
        return {"status": "unknown", "message": f"有效期格式无法解析（{expiry_date}），请注意核对"}
    year = int(m.group(1))
    month = int(m.group(2) or 12)
    day = int(m.group(3) or 28)
    try:
        expire_at = datetime(year, min(month, 12), min(day, 28), tzinfo=UTC)
    except ValueError:
        return {"status": "unknown", "message": f"有效期无法解析（{expiry_date}），请注意核对"}

    now = datetime.now(UTC)
    if expire_at <= now:
        return {"status": "expired", "message": f"🔴 该药品已过期（有效期至 {expiry_date}），请勿服用"}
    months_left = (expire_at.year - now.year) * 12 + (expire_at.month - now.month)
    if months_left <= _EXPIRING_SOON_MONTHS:
        return {"status": "expiring", "message": f"⚠️ 该药品将于 {expiry_date} 到期，请尽快核对剩余用量"}
    return {"status": "ok", "message": f"有效期至 {expiry_date}，正常使用期内"}


def check_interactions(scanned_name: str, existing_names: list[str], rules: dict | None = None) -> list[dict]:
    """复用健康卫士的相互作用检测，只保留涉及本次扫描药品的提示。"""
    if not scanned_name:
        return []
    from app.services.health_engine import _check_drug_interactions

    meds_raw = [{"name": scanned_name}] + [
        {"name": n} for n in existing_names if n and n != scanned_name
    ]
    warnings = _check_drug_interactions(meds_raw, user_name="")
    return [
        w for w in warnings
        if any(scanned_name in str(e.get("name", "")) for e in w.get("evidence", []))
    ]


def build_scan_result(drug: dict, existing_med_names: list[str], source: str = "mock") -> dict:
    """识别结果 + 富化 → 前端/智能体统一响应结构。"""
    if drug.get("not_a_drug"):
        return {"not_a_drug": True, "detected": drug.get("detected", ""), "source": source}

    rules = load_drug_rules()
    interactions = check_interactions(drug.get("generic_name", ""), existing_med_names, rules)
    return {
        "not_a_drug": False,
        "drug": drug,
        "category": categorize_drug(drug.get("generic_name", ""), rules),
        "expiry": check_expiry(drug.get("expiry_date", "")),
        "interactions": interactions,
        "source": source,
        "registered": False,
        "confirm_prompt": "是否将该药加入您的用药记录？确认后它会纳入后续相互作用核查与健康分析。",
    }


# ============================================================
# 用户确认后的登记
# ============================================================

async def register_drug(db, user_id: str | int, drug: dict, category: str = "") -> dict:
    """把已识别的药品写入 medication_records（仅在用户明确确认后调用）。"""
    from app import crud
    from app.models import MedicationRecord

    user = await crud.get_user(db, user_id)
    if user is None:
        raise ValueError("用户不存在，无法登记用药记录")

    name = (drug.get("generic_name") or drug.get("brand_name") or "").strip() or "未知药品"
    record = MedicationRecord(
        user_id=user.id,
        date=datetime.now(UTC),
        medication_name=name,
        category=category or categorize_drug(name) or "其他",
        quantity=1,
        unit_price=0.0,
        is_chronic=False,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return {"record_id": record.id, "medication_name": name, "category": record.category}


# ============================================================
# 展示层
# ============================================================

def format_chat_response(result: dict) -> str:
    """把 scan 结果格式化为面向用户的 Markdown（药品卫士口吻）。"""
    if result.get("not_a_drug"):
        detected = result.get("detected") or "其他资料"
        return (
            f"这张图片看起来不是药品包装（识别为：{detected}）。\n\n"
            "药品识别请上传**药盒、药品包装或说明书**的清晰照片。"
        )

    drug = result["drug"]
    lines = ["## 💊 药品识别结果"]

    name = drug.get("generic_name") or "未识别到药品名称"
    brand = f"（{drug['brand_name']}）" if drug.get("brand_name") else ""
    spec = f" {drug['spec']}" if drug.get("spec") else ""
    lines.append(f"**{name}**{brand}{spec}")
    if result.get("category"):
        lines.append(f"- 类别：{result['category']}")
    for label, key in (
        ("生产企业", "manufacturer"), ("批准文号", "approval_number"),
        ("批号", "batch_number"), ("有效期", "expiry_date"), ("类别标识", "otc_or_rx"),
    ):
        if drug.get(key):
            lines.append(f"- {label}：{drug[key]}")

    expiry = result.get("expiry") or {}
    lines.append(f"\n### 有效期核验\n{expiry.get('message', '包装上未可见有效期')}")

    interactions = result.get("interactions") or []
    if interactions:
        lines.append("\n### ⚠️ 用药相互作用提示")
        for w in interactions[:3]:
            icon = w.get("icon", "🟡")
            lines.append(f"- {icon} **{w.get('title', '联用提示')}**：{w.get('description', '')}")
            if w.get("suggestion"):
                lines.append(f"  建议：{w['suggestion']}")
    else:
        lines.append("\n### 安全提示\n未检测到与您现有用药记录的相互作用（如已登记）。")

    if drug.get("notes"):
        lines.append(f"\n> 识别说明：{drug['notes']}")
    if result.get("source") == "mock":
        lines.append("\n> ⚠️ 识别服务暂不可用，以上为示例数据。")

    lines.append(f"\n{result.get('confirm_prompt', '')}")
    return "\n".join(lines)
