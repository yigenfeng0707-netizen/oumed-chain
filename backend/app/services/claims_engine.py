"""
MedSignal - 报销计算引擎（Claims Engine）

P1-1：完整的、可解释的医保报销计算引擎。
- 输入：费用总额 + 费用明细（甲/乙/自费分类）+ 参保类型 + 医院等级 + 调整因子
- 输出：分步推导的 JSON（每步含 name/detail/amount）+ 自然语言解释 + 大病保险分段计算

算法链路：
  费用总额 → ①分类(甲/乙/自费) → ②乙类先自付 → ③进入统筹 →
  ④扣起付线 → ⑤按比例报销 → ⑥封顶线 → ⑦大病保险二次报销 → ⑧最终报销额/自付额

这是路演场景 2 "逐项公式推导" 的代码支撑。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 规则库路径：app/services/claims_engine.py → 上3层到 yibao-zhinao/，再进 data/
_RULES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data", "reimbursement_rules.json",
)
_RULES_CACHE: dict | None = None


def load_rules() -> dict:
    """加载报销规则库（带缓存）。"""
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    try:
        with open(_RULES_PATH, encoding="utf-8") as f:
            _RULES_CACHE = json.load(f)
        logger.info("加载报销规则库: %s", _RULES_PATH)
    except Exception as e:
        logger.error("加载报销规则库失败 %s: %s，使用内置兜底规则", _RULES_PATH, e)
        _RULES_CACHE = _builtin_rules()
    return _RULES_CACHE


@dataclass
class FeeItem:
    """费用明细项"""
    name: str
    amount: float
    category: str = "甲类"  # 甲类 / 乙类 / 自费


@dataclass
class ClaimsInput:
    """报销计算输入"""
    total_amount: float
    visit_type: str = "门诊"             # 门诊 / 住院
    insurance_type: str = "职工医保"     # 职工医保 / 居民医保 / 灵活就业医保
    hospital_level: str = "二级"          # 社区 / 二级 / 三级
    employee_status: str = "在职"          # 在职 / 退休
    items: list[FeeItem] = field(default_factory=list)  # 费用明细（空则按全甲类处理）
    chronic_disease: bool = False        # 是否享受门诊慢病待遇
    cross_region: bool = False           # 是否异地就医
    annual_used_deductible: float = 0    # 年度已用起付线累计


@dataclass
class ClaimsResult:
    """报销计算结果"""
    total_amount: float
    class_a: float          # 甲类费用
    class_b: float          # 乙类费用
    self_paid_items: float  # 自费项目
    class_b_deduction: float  # 乙类先自付
    pooling_base: float      # 进入统筹基数
    deductible: int          # 起付线
    annual_used_deductible: float
    effective_deductible: float  # 实际扣除的起付线
    rate: float              # 报销比例
    chronic_boost: float     # 慢病加成
    cross_region_penalty: float  # 异地扣减
    reimbursed_basic: float  # 基本医保报销额
    cap: int                 # 封顶线
    capped: bool             # 是否触及封顶
    big_insurance: float     # 大病保险报销额
    big_insurance_tiers: list[dict]  # 大病分段明细
    total_reimbursed: float  # 总报销额（基本 + 大病）
    out_of_pocket: float     # 个人自付
    reimbursement_ratio: float  # 实际报销比例
    steps: list[dict]        # 分步推导
    explanation: str         # 自然语言解释

    def to_dict(self) -> dict:
        return {
            "total_amount": round(self.total_amount, 2),
            "class_a": round(self.class_a, 2),
            "class_b": round(self.class_b, 2),
            "self_paid_items": round(self.self_paid_items, 2),
            "class_b_deduction": round(self.class_b_deduction, 2),
            "reimbursable_amount": round(self.pooling_base, 2),
            "deductible": self.deductible,
            "annual_used_deductible": round(self.annual_used_deductible, 2),
            "effective_deductible": round(self.effective_deductible, 2),
            "reimbursement_ratio": round(self.rate, 4),
            "chronic_boost": self.chronic_boost,
            "cross_region_penalty": self.cross_region_penalty,
            "reimbursed_basic": round(self.reimbursed_basic, 2),
            "cap": self.cap,
            "capped": self.capped,
            "big_insurance": round(self.big_insurance, 2),
            "big_insurance_tiers": self.big_insurance_tiers,
            "estimated_reimbursement": round(self.total_reimbursed, 2),
            "out_of_pocket": round(self.out_of_pocket, 2),
            "actual_ratio": round(self.reimbursement_ratio, 4),
            "steps": self.steps,
            "explanation": self.explanation,
        }


def calculate(inp: ClaimsInput) -> ClaimsResult:
    """执行完整的报销计算，返回带分步推导的结果。"""
    rules = load_rules()
    ins_cfg = rules["insurance_types"].get(inp.insurance_type, rules["insurance_types"]["职工医保"])
    adj = rules.get("adjustments", {})

    # ---- 步骤 1：费用分类（甲/乙/自费）----
    if inp.items:
        class_a = sum(it.amount for it in inp.items if it.category == "甲类")
        class_b = sum(it.amount for it in inp.items if it.category == "乙类")
        self_paid = sum(it.amount for it in inp.items if it.category == "自费")
        # 校正：明细总额可能与 total_amount 不一致，以明细为准
        total = class_a + class_b + self_paid
    else:
        # 无明细：假设全部为甲类（演示常见场景）
        class_a = inp.total_amount
        class_b = 0.0
        self_paid = 0.0
        total = inp.total_amount

    # ---- 步骤 2：乙类先自付 ----
    b_self_pay_rate = ins_cfg.get("class_b_self_pay_rate", 0.10)
    class_b_deduction = class_b * b_self_pay_rate

    # ---- 步骤 3：进入统筹基数 ----
    pooling_base = class_a + class_b * (1 - b_self_pay_rate)

    # ---- 步骤 4：起付线 ----
    rule = _lookup_rule(rules, inp.insurance_type, inp.visit_type, inp.hospital_level)
    deductible = rule["deductible"]
    # 年度累计起付线：剩余可扣 = max(0, deductible - annual_used)
    remaining_deductible = max(0, deductible - inp.annual_used_deductible)
    effective_deductible = min(remaining_deductible, pooling_base)

    # ---- 步骤 5：报销比例 + 调整因子 ----
    rate = rule["rate"]
    chronic_boost = 0.0
    cross_region_penalty = 0.0

    if inp.chronic_disease:
        chronic_boost = ins_cfg.get("chronic_benefit_boost", 0.10)
        rate += chronic_boost
    if inp.cross_region:
        cross_region_penalty = adj.get("cross_region_penalty", -0.05)
        rate += cross_region_penalty
    if "退休" in inp.employee_status:
        rate += ins_cfg.get("retiree_boost", 0.05)

    rate = max(adj.get("rate_floor", 0.50), min(rate, adj.get("rate_ceiling", 0.96)))

    # ---- 步骤 6：基本医保报销 + 封顶线 ----
    after_deductible = max(0, pooling_base - effective_deductible)
    cap = rule["cap"]
    reimbursed_basic = min(after_deductible * rate, cap)
    capped = after_deductible * rate > cap

    # ---- 步骤 7：大病保险二次报销 ----
    # 个人自付 = 总额 - 基本报销（含乙类自付 + 自费 + 起付线 + 比例自付）
    basic_self_pay = total - reimbursed_basic
    big_cfg = rules.get("big_insurance", {})
    big_deductible = big_cfg.get("deductible", 15000)
    big_insurance = 0.0
    big_tiers = []
    if basic_self_pay > big_deductible:
        over = basic_self_pay - big_deductible
        prev_cap = 0
        for tier in big_cfg.get("tiers", []):
            tier_max = tier["max"]
            tier_rate = tier["rate"]
            seg = over - prev_cap if tier_max is None else min(over, tier_max) - prev_cap
            if seg > 0:
                seg_reimb = seg * tier_rate
                big_insurance += seg_reimb
                big_tiers.append({
                    "label": tier["label"],
                    "segment": round(seg, 2),
                    "rate": tier_rate,
                    "reimbursed": round(seg_reimb, 2),
                })
            if tier_max is not None and over <= tier_max:
                break
            prev_cap = tier_max or prev_cap

    # ---- 步骤 8：汇总 ----
    total_reimbursed = reimbursed_basic + big_insurance
    out_of_pocket = total - total_reimbursed
    actual_ratio = total_reimbursed / total if total > 0 else 0

    # ---- 分步推导（路演逐项公式推导的代码支撑）----
    steps = _build_steps(
        total=total, class_a=class_a, class_b=class_b, self_paid=self_paid,
        b_rate=b_self_pay_rate, b_deduction=class_b_deduction,
        pooling_base=pooling_base, deductible=deductible,
        annual_used=inp.annual_used_deductible, effective_ded=effective_deductible,
        rate=rate, chronic_boost=chronic_boost, cross_region_penalty=cross_region_penalty,
        employee_status=inp.employee_status, after_ded=after_deductible,
        reimbursed_basic=reimbursed_basic, cap=cap, capped=capped,
        basic_self_pay=basic_self_pay, big_deductible=big_deductible,
        big_insurance=big_insurance, total_reimbursed=total_reimbursed,
        out_of_pocket=out_of_pocket,
    )

    explanation = _build_explanation(
        inp=inp, rate=rate, deductible=deductible, cap=cap,
        reimbursed_basic=reimbursed_basic, big_insurance=big_insurance,
        total_reimbursed=total_reimbursed, out_of_pocket=out_of_pocket,
    )

    return ClaimsResult(
        total_amount=total, class_a=class_a, class_b=class_b,
        self_paid_items=self_paid, class_b_deduction=class_b_deduction,
        pooling_base=pooling_base, deductible=deductible,
        annual_used_deductible=inp.annual_used_deductible,
        effective_deductible=effective_deductible, rate=rate,
        chronic_boost=chronic_boost, cross_region_penalty=cross_region_penalty,
        reimbursed_basic=reimbursed_basic, cap=cap, capped=capped,
        big_insurance=big_insurance, big_insurance_tiers=big_tiers,
        total_reimbursed=total_reimbursed, out_of_pocket=out_of_pocket,
        reimbursement_ratio=actual_ratio, steps=steps, explanation=explanation,
    )


def compare_scenarios(inp: ClaimsInput) -> list[dict]:
    """对比不同医院等级 / 慢病待遇 / 异地场景的报销差异（数据赋能价值展示）"""
    scenarios = []
    for level in ["社区", "二级", "三级"]:
        for chronic in ([False, True] if not inp.chronic_disease else [True]):
            for cross in ([False, True] if inp.cross_region else [False]):
                sub_inp = ClaimsInput(
                    total_amount=inp.total_amount, visit_type=inp.visit_type,
                    insurance_type=inp.insurance_type, hospital_level=level,
                    employee_status=inp.employee_status, items=inp.items,
                    chronic_disease=chronic, cross_region=cross,
                    annual_used_deductible=inp.annual_used_deductible,
                )
                r = calculate(sub_inp)
                tag = f"{level}"
                if chronic:
                    tag += "+慢病"
                if cross:
                    tag += "+异地"
                scenarios.append({
                    "scenario": tag,
                    "hospital_level": level,
                    "chronic_disease": chronic,
                    "cross_region": cross,
                    "reimbursed": round(r.total_reimbursed, 2),
                    "out_of_pocket": round(r.out_of_pocket, 2),
                    "actual_ratio": round(r.reimbursement_ratio, 4),
                })
    return scenarios


# ============================================================
# 内部工具
# ============================================================

def _lookup_rule(rules: dict, ins_type: str, visit_type: str, level: str) -> dict:
    """查表获取起付线/比例/封顶线。"""
    ins_rules = rules["rules"].get(ins_type) or rules["rules"]["职工医保"]
    visit_rules = ins_rules.get(visit_type) or ins_rules["住院"]
    return visit_rules.get(level) or visit_rules["二级"]


def _build_steps(**kw) -> list[dict]:
    """构建分步推导明细。"""
    s = []
    s.append({"name": "① 费用分类", "detail": kw["total"], "amount": round(kw["total"], 2)})

    if kw["class_b"] > 0 or kw["self_paid"] > 0:
        detail = f"甲类 {kw['class_a']:.2f} 元、乙类 {kw['class_b']:.2f} 元、自费 {kw['self_paid']:.2f} 元"
    else:
        detail = f"门诊/住院费用 {kw['total']:.2f} 元（均为医保目录内甲类）"
    s.append({"name": "② 费用明细", "detail": detail, "amount": round(kw["total"], 2)})

    if kw["b_deduction"] > 0:
        s.append({"name": "③ 乙类先自付", "detail": f"乙类 {kw['class_b']:.2f} × {kw['b_rate']*100:.0f}% = {kw['b_deduction']:.2f} 元", "amount": round(kw["b_deduction"], 2)})

    s.append({"name": "④ 进入统筹", "detail": f"{kw['total']:.2f} - {kw['b_deduction']:.2f} - {kw['self_paid']:.2f} = {kw['pooling_base']:.2f} 元", "amount": round(kw["pooling_base"], 2)})

    ded_detail = f"起付线 {kw['deductible']} 元（年度已用 {kw['annual_used']:.2f}，本次扣 {kw['effective_ded']:.2f}）"
    s.append({"name": "⑤ 扣起付线", "detail": ded_detail, "amount": round(kw["effective_ded"], 2)})

    rate_detail = f"报销比例 {kw['rate']*100:.0f}%"
    boosts = []
    if kw["chronic_boost"] > 0:
        boosts.append(f"慢病待遇+{kw['chronic_boost']*100:.0f}%")
    if kw["cross_region_penalty"] < 0:
        boosts.append(f"异地扣减{kw['cross_region_penalty']*100:.0f}%")
    if "退休" in kw["employee_status"]:
        boosts.append("退休加成+5%")
    if boosts:
        rate_detail += f"（{'、'.join(boosts)}）"
    s.append({"name": "⑥ 报销计算", "detail": f"{rate_detail}：{kw['after_ded']:.2f} × {kw['rate']*100:.0f}% = {kw['reimbursed_basic']:.2f} 元", "amount": round(kw["reimbursed_basic"], 2)})

    cap_detail = f"封顶线 {kw['cap']} 元" + ("（已触及）" if kw["capped"] else "（未触及）")
    s.append({"name": "⑦ 封顶线检查", "detail": cap_detail, "amount": kw["cap"]})

    if kw["big_insurance"] > 0:
        s.append({"name": "⑧ 大病保险", "detail": f"个人自付 {kw['basic_self_pay']:.2f} 超过大病起付线 {kw['big_deductible']}，二次报销 {kw['big_insurance']:.2f} 元", "amount": round(kw["big_insurance"], 2)})

    s.append({"name": "⑨ 最终结果", "detail": f"总报销 {kw['total_reimbursed']:.2f} 元，个人自付 {kw['out_of_pocket']:.2f} 元", "amount": round(kw["total_reimbursed"], 2)})

    return s


def _build_explanation(inp: ClaimsInput, rate: float, deductible: int, cap: int,
                       reimbursed_basic: float, big_insurance: float,
                       total_reimbursed: float, out_of_pocket: float) -> str:
    parts = [
        f"根据您的{inp.insurance_type}待遇，{inp.hospital_level}医院{inp.visit_type}起付线 {deductible} 元，报销比例 {rate*100:.0f}%。"
    ]
    boosts = []
    if inp.chronic_disease:
        boosts.append("门诊慢病待遇")
    if inp.cross_region:
        boosts.append("异地就医")
    if "退休" in inp.employee_status:
        boosts.append("退休人员")
    if boosts:
        parts.append(f"已应用调整：{'、'.join(boosts)}。")
    parts.append(
        f"本次费用 {inp.total_amount:.2f} 元，基本医保报销 {reimbursed_basic:.2f} 元"
        + (f"，大病保险二次报销 {big_insurance:.2f} 元" if big_insurance > 0 else "")
        + f"，合计报销 {total_reimbursed:.2f} 元，个人自付 {out_of_pocket:.2f} 元（实际报销比例 {total_reimbursed/inp.total_amount*100:.1f}%）。"
    )
    return "".join(parts)


def _builtin_rules() -> dict:
    """规则库加载失败时的内置兜底。"""
    return {
        "insurance_types": {
            "职工医保": {"class_b_self_pay_rate": 0.10, "chronic_benefit_boost": 0.10, "retiree_boost": 0.05},
            "居民医保": {"class_b_self_pay_rate": 0.15, "chronic_benefit_boost": 0.08, "retiree_boost": 0.05},
        },
        "rules": {
            "职工医保": {
                "门诊": {
                    "社区": {"deductible": 500, "rate": 0.80, "cap": 5000},
                    "二级": {"deductible": 800, "rate": 0.75, "cap": 5000},
                    "三级": {"deductible": 1000, "rate": 0.65, "cap": 5000},
                },
                "住院": {
                    "社区": {"deductible": 300, "rate": 0.92, "cap": 300000},
                    "二级": {"deductible": 600, "rate": 0.88, "cap": 300000},
                    "三级": {"deductible": 1000, "rate": 0.85, "cap": 300000},
                },
            },
            "居民医保": {
                "门诊": {
                    "社区": {"deductible": 200, "rate": 0.60, "cap": 3000},
                    "二级": {"deductible": 400, "rate": 0.50, "cap": 3000},
                    "三级": {"deductible": 800, "rate": 0.45, "cap": 3000},
                },
                "住院": {
                    "社区": {"deductible": 200, "rate": 0.85, "cap": 200000},
                    "二级": {"deductible": 500, "rate": 0.70, "cap": 200000},
                    "三级": {"deductible": 1000, "rate": 0.60, "cap": 200000},
                },
            },
        },
        "big_insurance": {
            "deductible": 15000,
            "tiers": [
                {"max": 50000, "rate": 0.60, "label": "0-5万段"},
                {"max": 100000, "rate": 0.70, "label": "5-10万段"},
                {"max": None, "rate": 0.80, "label": "10万以上段"},
            ],
        },
        "adjustments": {
            "cross_region_penalty": -0.05, "rate_floor": 0.50, "rate_ceiling": 0.96,
        },
    }


# ----------------------------------------------------------------------
# 上传资料联合预审（报销助手 × 档案管家 协同）
# ----------------------------------------------------------------------

_INVOICE_WORDS = ("发票", "票据", "价税合计", "收费凭证", "结算单", "invoice", "receipt")
_LIST_WORDS = ("费用清单", "明细清单", "费用明细", "收费明细")
# 住院/门诊费用清单常见费用项目词（OCR 文本常缺失“清单”字样，用项目词兼容）
_FEE_ITEM_WORDS = ("床位费", "护理费", "诊查费", "西药费", "中成药费", "中草药费",
                   "治疗费", "检查费", "化验费", "材料费", "手术费", "输液费")
# 金额前缀词（含小写合计与传统字形）；允许较长间隔以跨过“大写…”等干扰文本
_AMOUNT_RE = re.compile(r"(?:价税合计|金额合计|小写|合計|金额|金額|总额|總額|合计)[^\d\n]{0,12}([0-9]+(?:\.[0-9]{1,2})?)")

REQUIRED_DOC_CATEGORIES = ("发票/票据", "费用清单", "病历文本", "检查报告")


def classify_uploaded_doc(filename: str, doc_kind: str, text: str) -> str:
    """面向报销流程的存档资料二次分类：发票 / 费用清单 / 病历文本 / 检查报告 / 其他。

    优先级：强发票信号（价税合计/票据号码类） > 强清单信号（清单/明细/分类小计/费用项目词）
    > 弱发票词（发票/票据字样）。费用清单常带“发票”水印字样，不可仅凭该词判发票。
    """
    t = text or ""
    name = filename or ""
    strong_invoice = any(w in t for w in ("价税合计", "收费凭证", "结算单", "票据代码", "票据号码")) \
        or any(w in name for w in ("发票", "receipt", "invoice"))
    if strong_invoice:
        return "发票/票据"
    fee_hits = sum(1 for w in _FEE_ITEM_WORDS if w in t)
    if any(w in t for w in _LIST_WORDS) or "清单" in t or "分类小计" in t \
            or "清单" in name or "明细" in name or fee_hits >= 2:
        return "费用清单"
    if any(w in t for w in _INVOICE_WORDS):
        return "发票/票据"
    if doc_kind in ("CT报告", "MRI报告"):
        return "检查报告"
    if doc_kind == "病历文本":
        return "病历文本"
    return "其他"


def extract_invoice_amount(text: str) -> float | None:
    """从发票文本提取合计金额；多个候选取最大值。"""
    vals: list[float] = []
    for v in _AMOUNT_RE.findall(text or ""):
        try:
            vals.append(float(v))
        except ValueError:
            continue
    return max(vals) if vals else None


def summarize_doc_content(text: str, max_len: int = 60) -> str:
    """从转录文本提炼一行主要内容（供预审回复引用，避免只报分类不报内容）。"""
    parts = [seg.strip() for seg in (text or "").split() if len(seg.strip()) >= 2]
    if not parts:
        return ""
    brief = "、".join(parts)
    return brief[:max_len] + ("…" if len(brief) > max_len else "")


def clean_doc_content(text: str, max_len: int = 300) -> str:
    """转录文本清理为可读摘录（分步明细中逐字引用用）。"""
    joined = " ".join((text or "").split())
    return joined[:max_len] + ("…" if len(joined) > max_len else "")


def review_uploaded_documents(docs: list[dict], insurance_type: str = "职工医保") -> dict:
    """上传资料联合预审：二次分类 + 金额提取 + 完整性核对 + 报销引擎测算。

    docs: [{"filename", "doc_kind", "extracted_text"}]
    返回 documents / total_amount / completeness / estimate / response（报销助手意见文本）。
    """
    classified: list[dict] = []
    total = 0.0
    for d in docs:
        kind = classify_uploaded_doc(d.get("filename", ""), d.get("doc_kind", ""), d.get("extracted_text", ""))
        amount = extract_invoice_amount(d.get("extracted_text", "")) if kind == "发票/票据" else None
        if amount is not None:
            total += amount
        classified.append({
            "filename": d.get("filename", ""),
            "archive_kind": d.get("doc_kind", ""),
            "claim_kind": kind,
            "amount": amount,
            "content_brief": summarize_doc_content(d.get("extracted_text", "")),
            "content_full": clean_doc_content(d.get("extracted_text", "")),
        })

    present = {c["claim_kind"] for c in classified}
    completeness = [
        {"name": req, "status": "uploaded" if req in present else "missing"}
        for req in REQUIRED_DOC_CATEGORIES
    ]
    missing = [c["name"] for c in completeness if c["status"] == "missing"]

    estimate = None
    if total > 0:
        estimate = calculate(ClaimsInput(
            total_amount=total, visit_type="门诊", insurance_type=insurance_type,
            hospital_level="二级", chronic_disease=False,
        )).to_dict()

    read_lines = []
    for c in classified:
        line = f"- 《{c['filename']}》识别为{c['claim_kind']}"
        if c["amount"] is not None:
            line += f"，金额 {c['amount']:.2f} 元"
        if c["content_brief"]:
            line += f"\n  内容摘要：{c['content_brief']}"
        read_lines.append(line)

    parts = ["从您上传的资料中读到：", "\n".join(read_lines)]
    parts.append(
        "\n材料完整性核对：\n"
        + "\n".join(f"{'✅' if c['status'] == 'uploaded' else '❌'} {c['name']}" for c in completeness)
    )
    if missing:
        parts.append(f"\n缺少：{'、'.join(missing)}。可后续补传，不影响已上传材料的预审。")
    if estimate is not None:
        parts.append(
            f"\n预审测算（发票合计 {total:.2f} 元，{insurance_type}·门诊·二级医院）："
            f"统筹预计支付约 {estimate['estimated_reimbursement']:.2f} 元，"
            f"个人负担约 {estimate['out_of_pocket']:.2f} 元。"
        )
    else:
        parts.append("\n未从发票中读取到金额，暂不做测算；补传含金额的发票后可自动测算。")
    parts.append("\nℹ️ 以上为 AI 预审意见，正式审核由医保经办机构完成，办理时限 15–30 个工作日。")

    return {
        "documents": classified,
        "total_amount": round(total, 2) if total else None,
        "completeness": completeness,
        "estimate": estimate,
        "response": "\n".join(parts),
    }


async def _recent_review_payload(db, user_id: str, limit: int, within_minutes: int):
    """加载用户近期存档资料与险种；无近期资料返回 None。"""
    from app import crud

    docs = await crud.list_recent_body_documents(db, user_id, limit=limit, within_minutes=within_minutes)
    if not docs:
        return None

    insurance_type = "职工医保"
    try:
        user = await crud.get_user(db, user_id)
        if user is not None and getattr(user, "insurance_type", None):
            insurance_type = user.insurance_type
    except Exception:
        pass

    payload = [
        {"filename": d.filename, "doc_kind": d.doc_kind, "extracted_text": d.extracted_text}
        for d in docs
    ]
    return payload, insurance_type


async def build_uploaded_prereview(db, user_id: str, limit: int = 10,
                                   within_minutes: int = 120) -> dict | None:
    """读取用户最近存档的上传资料并联合预审；无近期资料返回 None。

    返回 {response, documents, total_amount, completeness, estimate}，
    response 为 档案管家存档汇总 × 报销助手预审意见 的融合文本。
    """
    loaded = await _recent_review_payload(db, user_id, limit, within_minutes)
    if not loaded:
        return None
    payload, insurance_type = loaded

    review = review_uploaded_documents(payload, insurance_type=insurance_type)
    archive_lines = "\n".join(f"✅ 《{d['filename']}》（{d['doc_kind']}）已存档" for d in payload)
    review["response"] = (
        "**【档案管家】**\n"
        f"{archive_lines}\n\n---\n\n"
        f"**【报销助手】**\n{review['response']}"
    )
    return review


def build_prereview_detail_text(review: dict, insurance_type: str) -> str:
    """将预审结果展开为分步推导明细（追问“具体细节/怎么算”时使用）。"""
    estimate = review.get("estimate")
    missing = [c["name"] for c in review["completeness"] if c["status"] == "missing"]

    lines = ["把刚才的预审意见展开说明：", "\n**📄 资料识别明细**"]
    for c in review["documents"]:
        line = f"- 《{c['filename']}》：存档类型“{c['archive_kind']}” → 报销材料类型“{c['claim_kind']}”"
        if c["amount"] is not None:
            line += f"，提取金额 {c['amount']:.2f} 元"
        lines.append(line)
        if c.get("content_full"):
            lines.append(f"  > 识别到的内容：{c['content_full']}")

    if estimate:
        e = estimate
        lines.append(f"\n**🧮 测算推导过程**（{insurance_type}·门诊·二级医院）")
        all_class_a = e["class_b"] == 0 and e["self_paid_items"] == 0
        lines.append(
            f"1. 发票合计 {e['total_amount']:.2f} 元"
            + ("（未提供费用明细，按全额甲类估算）" if all_class_a else "")
        )
        if not all_class_a:
            lines.append(
                f"2. 费用分类：甲类 {e['class_a']:.2f} 元、乙类 {e['class_b']:.2f} 元、自费 {e['self_paid_items']:.2f} 元；"
                f"乙类先自付 {e['class_b_deduction']:.2f} 元"
            )
        after_deductible = max(0.0, e["reimbursable_amount"] - e["effective_deductible"])
        lines.append(f"3. 进入统筹基数 {e['reimbursable_amount']:.2f} 元，扣除起付线 {e['effective_deductible']:.2f} 元，剩余 {after_deductible:.2f} 元")
        lines.append(
            f"4. 按报销比例 {e['reimbursement_ratio'] * 100:.1f}% 计算："
            f"基本医保统筹支付 {after_deductible:.2f} × {e['reimbursement_ratio'] * 100:.0f}% = {e['reimbursed_basic']:.2f} 元"
        )
        if e["capped"]:
            lines.append(f"5. 已触及封顶线 {e['cap']:.0f} 元，超出部分由个人负担")
        if e["big_insurance"] > 0:
            tier_desc = "；".join(
                f"{t['label']} {t['segment']:.2f} 元 × {t['rate'] * 100:.0f}% = {t['reimbursed']:.2f} 元"
                for t in e["big_insurance_tiers"]
            )
            lines.append(f"5. 大病保险二次报销 {e['big_insurance']:.2f} 元（{tier_desc}）")
        else:
            big_deductible = load_rules().get("big_insurance", {}).get("deductible", 15000)
            lines.append(f"5. 大病保险：个人自付未达到大病起付线 {big_deductible:.0f} 元，未触发二次报销")
        # 个人负担构成（起付线 + 乙类先自付 + 自费 + 比例自付）
        ratio_self_pay = max(0.0, after_deductible - e["reimbursed_basic"])
        composition = [f"起付线 {e['effective_deductible']:.2f} 元"]
        if e["class_b_deduction"]:
            composition.append(f"乙类先自付 {e['class_b_deduction']:.2f} 元")
        if e["self_paid_items"]:
            composition.append(f"自费项目 {e['self_paid_items']:.2f} 元")
        if ratio_self_pay:
            composition.append(f"比例自付 {ratio_self_pay:.2f} 元")
        lines.append(
            f"6. 汇总：统筹预计支付 {e['estimated_reimbursement']:.2f} 元；"
            f"个人负担 {e['out_of_pocket']:.2f} 元 = {' + '.join(composition)}"
        )
        lines.append(
            f"\n**📋 规则依据**：{insurance_type}·门诊·二级医院 —— 起付线 {e['deductible']} 元，"
            f"报销比例 {e['reimbursement_ratio'] * 100:.0f}%，年度封顶线 {e['cap']:.0f} 元。"
        )
    else:
        lines.append("\n未从发票中提取到金额，暂无法展开测算；补传含合计金额的发票后可自动推导。")
    if missing:
        lines.append(f"\n材料提醒：仍缺少{'、'.join(missing)}，补传不影响已上传材料的预审。")
    lines.append("\nℹ️ 以上为 AI 预审意见，正式审核由医保经办机构完成，办理时限 15–30 个工作日。")
    return "\n".join(lines)


async def build_uploaded_prereview_detail(db, user_id: str, limit: int = 10,
                                          within_minutes: int = 120) -> dict | None:
    """预审结果的分步推导明细版（追问“具体细节/怎么算”时用）；无近期资料返回 None。"""
    loaded = await _recent_review_payload(db, user_id, limit, within_minutes)
    if not loaded:
        return None
    payload, insurance_type = loaded

    review = review_uploaded_documents(payload, insurance_type=insurance_type)
    review["response"] = "**【报销助手】**\n" + build_prereview_detail_text(review, insurance_type)
    return review
