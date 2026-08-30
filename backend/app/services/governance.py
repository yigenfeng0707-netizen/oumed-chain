"""AI 病历治理引擎（瓯医数链 · 数据供给侧核心能力）

两级治理流水线：
1. PHI 脱敏（规则引擎，确定性，零依赖）：身份证/手机号/姓名/日期等敏感实体识别与掩码
2. 病历结构化（LLM，本地 Ollama 优先）：非结构化入院记录 → 标准化 JSON 数据集

设计约束：治理全程在院内网完成（本地模型），只有脱敏后的结构化结果才可进入
联邦协作或数据产品流通环节。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------- PHI 敏感实体识别（规则层） ----------------

PHI_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("身份证号", re.compile(r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b")),
    ("手机号", re.compile(r"\b1[3-9]\d{9}\b")),
    ("座机", re.compile(r"\b0\d{2,3}-\d{7,8}\b")),
    ("住院号", re.compile(r"(?:住院号|病历号|登记号)[:：\s]*([A-Za-z0-9]{6,20})")),
    ("银行卡号", re.compile(r"\b[3-6]\d{15,18}\b")),
    ("邮箱", re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")),
]

# 常见中文姓名语境（称谓前缀法）：X先生/X女士/X某/患者X
_NAME_CONTEXT = re.compile(
    r"(?:患者|病人|张|李|王|刘|陈|杨|赵|黄|周|吴|徐|孙|胡|朱|高|林|何|郭|马)(?:先生|女士|阿姨|大爷|大叔|某|某某)"
)

_MASK = "█"


@dataclass
class DeidResult:
    masked_text: str
    entities: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "masked_text": self.masked_text,
            "entities": self.entities,
            "entity_count": len(self.entities),
        }


def deidentify(text: str) -> DeidResult:
    """规则层 PHI 脱敏：确定性、可审计、不依赖模型。"""
    spans: list[tuple[int, int, str]] = []

    for label, pattern in PHI_PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.span()
            # 住院号只掩码捕获组
            if label == "住院号" and m.group(1):
                s = start + m.group(0).index(m.group(1))
                spans.append((s, s + len(m.group(1)), label))
            else:
                spans.append((start, end, label))

    for m in _NAME_CONTEXT.finditer(text):
        core = m.group(0)
        # 姓氏保留，其余掩码（保留称谓）
        if core[-2:] in ("先生", "女士", "阿姨", "大爷", "大叔"):
            keep = 1
        elif core.endswith("某某"):
            keep = 1
        else:
            keep = 1
        name_start = m.start() + (len(core) - len(core))  # 含姓氏整体
        spans.append((name_start + keep, m.end(), "姓名"))

    # 区间去重合并（后出现的覆盖先出现的场景：按起点排序从后往前掩码）
    spans = sorted(set(spans), key=lambda s: (s[0], -(s[1] - s[0])))
    merged: list[list[int]] = []
    for s, e, _ in spans:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    masked = text
    entities = []
    for s, e in reversed(merged):
        original = masked[s:e]
        masked = masked[:s] + _MASK * min(len(original), 8) + masked[e:]
        entities.insert(0, {"start": s, "end": e, "masked": original})

    return DeidResult(masked_text=masked, entities=entities)


# ---------------- 病历结构化（LLM 层，本地 Ollama 优先） ----------------

STRUCTURE_PROMPT = """你是医院病历数据治理引擎。将下面的非结构化入院记录抽取为 JSON，字段如下：
{{
  "patient": {{"age": 数字或null, "sex": "男/女/null"}},
  "chief_complaint": "主诉",
  "diagnoses": ["诊断列表"],
  "vitals": {{"bp": "收缩压/舒张压 或 null", "heart_rate": 数字或null, "temperature": 数字或null}},
  "medications": [{{"name": "药名", "dose": "剂量"}}],
  "history": ["既往史要点"]
}}
只返回 JSON，不要解释。字段缺失填 null 或空数组。/no_think

入院记录：
{note}
"""

# 兜底规则抽取（LLM 不可用时仍可产出结构化结果）
_DIAG_PATTERN = re.compile(r"(?:入院诊断|初步诊断|诊断)[:：]\s*([^。；;\n]{2,60})")
_VITAL_BP = re.compile(r"血压[:：\s]*(\d{2,3})[/／](\d{2,3})")
_VITAL_HR = re.compile(r"心率[:：\s]*(\d{2,3})")
_AGE_SEX = re.compile(r"(\d{1,3})岁[，,]?\s*(男|女)")


def rule_structure(note: str) -> dict:
    diag = _DIAG_PATTERN.search(note)
    bp = _VITAL_BP.search(note)
    hr = _VITAL_HR.search(note)
    age_sex = _AGE_SEX.search(note)
    return {
        "patient": {
            "age": int(age_sex.group(1)) if age_sex else None,
            "sex": age_sex.group(2) if age_sex else None,
        },
        "chief_complaint": None,
        "diagnoses": (
            [d.strip() for d in re.split(r"[，,；;]", diag.group(1)) if d.strip()]
            if diag
            else []
        ),
        "vitals": {
            "bp": f"{bp.group(1)}/{bp.group(2)}" if bp else None,
            "heart_rate": int(hr.group(1)) if hr else None,
            "temperature": None,
        },
        "medications": [],
        "history": [],
        "extractor": "rules",
    }


def _extract_json_block(text: str) -> str | None:
    """括号配平扫描：取出文本中第一个完整且可解析的 {...} 块。

    比 find/rfind 健壮：能跳过思考块里的残缺 JSON 片段。
    """
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[i : j + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except json.JSONDecodeError:
                            break
        # 当前起点无有效块，继续找下一个 {
    return None


def _clean_structured(data: dict) -> dict:
    """LLM 输出的字段级清洗：修正常见小错（如血压 158/9:92 → 158/92）。"""
    vitals = data.get("vitals")
    if isinstance(vitals, dict) and vitals.get("bp"):
        nums = re.findall(r"\d{1,3}", str(vitals["bp"]))
        if len(nums) >= 2:
            vitals["bp"] = f"{nums[0]}/{nums[-1]}"  # 首个=收缩压，末个=舒张压
    for key in ("patient", "chief_complaint", "diagnoses", "vitals", "medications", "history"):
        data.setdefault(key, None if key in ("patient", "chief_complaint") else [])
    return data


def llm_structure(note: str) -> dict | None:
    """本地 Ollama 结构化（原生 /api/chat：think=false + format=json，约6-10秒）。

    失败返回 None 走规则兜底。
    """
    try:
        import httpx

        resp = httpx.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "qwen3:4b",
                "messages": [{
                    "role": "user",
                    "content": STRUCTURE_PROMPT.format(note=note[:3000]),
                }],
                "stream": False,
                "think": False,   # 结构化任务禁用思考模式（qwen3）
                "format": "json",  # Ollama JSON 语法约束采样
                "options": {"temperature": 0.1, "num_predict": 800},
            },
            timeout=150,  # 模型冷加载首次可达 35s+
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message") or {}).get("content") or ""
        content = content.strip()
        # format=json 下偶发约束失败会返回错误 JSON，先校验
        if not content.startswith("{"):
            logger.warning("LLM 输出非 JSON，降级规则引擎: %s", content[:150])
            return None
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        parsed["extractor"] = "llm:qwen3:4b"
        return _clean_structured(parsed)
    except Exception as e:  # noqa: BLE001
        logger.warning("LLM 结构化失败，降级规则引擎: %s", e)
        return None


def govern(note: str, use_llm: bool = True) -> dict:
    """完整治理流水线：脱敏 → 结构化。返回治理报告。"""
    deid = deidentify(note)
    structured = llm_structure(deid.masked_text) if use_llm else None
    if structured is None:
        structured = rule_structure(deid.masked_text)
    return {
        "deid": deid.to_dict(),
        "structured": structured,
        "pipeline": ["PHI脱敏(规则)", "结构化(LLM本地)" if structured.get("extractor", "").startswith("llm") else "结构化(规则兜底)"],
        "compliance": "治理全程院内网完成；仅脱敏后结构化结果可进入流通环节",
    }
