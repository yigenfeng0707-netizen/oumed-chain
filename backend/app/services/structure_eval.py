"""瓯医数链 - 病历结构化字段级评估（P2.5 生产化验收）

对 governance.llm_structure 的结构化输出做字段级对照评分：
评估集 200 条合成入院记录（data/structure_eval_set.json，字段级标注），
逐条对照标注计算 9 个叶子字段的正确率，总体字段准确率 ≥95% 为验收线。

评分口径（叶子字段，每条记录 9 个）：
    patient.age / patient.sex / chief_complaint
    vitals.bp / vitals.heart_rate / vitals.temperature
    diagnoses / medications / history
所有比较先做归一化（去空白/全半角标点/大小写），避免格式抖动误判；
集合类字段（诊断/用药/既往史）按多重集合匹配，多余或缺失都算错。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# 归一化时移除的字符：空白 + 中英文常用标点（保留 数值/单位/字母/汉字）
_PUNCT_RE = re.compile(r"[\s，。；：、！？·“”‘’（）()\[\]【】,.;:!?'\"]+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _norm(value) -> str:
    """通用归一化：字符串去空白与标点、转小写。"""
    if value is None:
        return ""
    return _PUNCT_RE.sub("", str(value)).lower()


def _text_match(gold: str, pred: str, fuzzy_ratio: float = 0.85) -> bool:
    """文本相等判定：归一化相等，或双向包含，或模糊相似度达阈。"""
    g, p = _norm(gold), _norm(pred)
    if not g:
        return p == ""
    if not p:
        return False
    if g == p or g in p or p in g:
        return True
    return SequenceMatcher(None, g, p).ratio() >= fuzzy_ratio


def _as_number(value):
    """从任意标量里提取数值（容忍 '58岁' / '36.8℃' / 58 等形态）。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    nums = _NUM_RE.findall(str(value))
    return float(nums[0]) if nums else None


def _bp_values(value) -> tuple[int, int] | None:
    """从血压字段提取（收缩压, 舒张压），如 '158/92' / '158/92mmHg'。"""
    if value is None:
        return None
    nums = _NUM_RE.findall(str(value))
    if len(nums) < 2:
        return None
    return int(float(nums[0])), int(float(nums[-1]))


def _match_med(gold_med: dict, pred_meds: list[dict], used: set[int]) -> int | None:
    """在预测用药列表里为一条标注用药找匹配（名称双向包含 + 剂量子串），返回索引。"""
    g_name, g_dose = _norm(gold_med.get("name")), _norm(gold_med.get("dose"))
    for i, pm in enumerate(pred_meds):
        if i in used:
            continue
        p_name, p_dose = _norm(pm.get("name") if isinstance(pm, dict) else pm), \
            _norm(pm.get("dose") if isinstance(pm, dict) else "")
        name_ok = bool(g_name and p_name) and (g_name == p_name or g_name in p_name or p_name in g_name)
        if not name_ok:
            continue
        dose_ok = (not g_dose) or (not p_dose) or g_dose in p_dose or p_dose in g_dose
        if dose_ok:
            return i
    return None


def _match_list(gold_items: list, pred_items: list) -> bool:
    """集合类字段判定：每个标注项都在预测里找到未占用的匹配，且数量一致。"""
    if not isinstance(pred_items, list):
        return False
    pred_norm = [_norm(x) for x in pred_items]
    used: set[int] = set()
    for g in gold_items:
        gn = _norm(g)
        hit = None
        for i, pn in enumerate(pred_norm):
            if i in used:
                continue
            if gn == pn or (gn and pn and (gn in pn or pn in gn)) or \
                    (gn and pn and SequenceMatcher(None, gn, pn).ratio() >= 0.85):
                hit = i
                break
        if hit is None:
            return False
        used.add(hit)
    return len(used) == len(pred_norm)


def score_record(pred: dict | None, gold: dict) -> dict:
    """对单条记录做字段级评分。

    Returns:
        {"fields": {叶子字段: 0/1}, "correct": int, "total": int}
        pred 为 None（提取彻底失败）时全部字段记 0。
    """
    p = pred if isinstance(pred, dict) else {}

    p_patient = p.get("patient") if isinstance(p.get("patient"), dict) else {}
    g_patient = gold.get("patient") or {}
    p_vitals = p.get("vitals") if isinstance(p.get("vitals"), dict) else {}
    g_vitals = gold.get("vitals") or {}

    # ---- 标量字段 ----
    g_age, p_age = g_patient.get("age"), _as_number(p_patient.get("age"))
    age_ok = (g_age is None and p_age is None) or \
        (g_age is not None and p_age is not None and abs(float(g_age) - float(p_age)) < 0.5)

    g_sex, p_sex = _norm(g_patient.get("sex")), _norm(p_patient.get("sex"))
    sex_ok = (not g_sex and not p_sex) or \
        (bool(g_sex) and bool(p_sex) and (g_sex[0] == p_sex[0]))  # “男性/男”同判

    g_cc = gold.get("chief_complaint")
    cc_ok = (g_cc is None and p.get("chief_complaint") is None) or \
        (g_cc is not None and _text_match(g_cc, p.get("chief_complaint") or ""))

    g_bp, p_bp = _bp_values(g_vitals.get("bp")), _bp_values(p_vitals.get("bp"))
    bp_ok = (g_bp is None and p_bp is None) or (g_bp is not None and p_bp == g_bp)

    g_hr, p_hr = g_vitals.get("heart_rate"), _as_number(p_vitals.get("heart_rate"))
    hr_ok = (g_hr is None and p_hr is None) or \
        (g_hr is not None and p_hr is not None and abs(float(g_hr) - float(p_hr)) < 0.5)

    g_temp, p_temp = g_vitals.get("temperature"), _as_number(p_vitals.get("temperature"))
    temp_ok = (g_temp is None and p_temp is None) or \
        (g_temp is not None and p_temp is not None and abs(float(g_temp) - float(p_temp)) <= 0.11)

    # ---- 集合字段 ----
    g_diag = gold.get("diagnoses") or []
    diag_ok = _match_list(g_diag, p.get("diagnoses") if isinstance(p.get("diagnoses"), list) else [])

    g_meds = gold.get("medications") or []
    p_meds = p.get("medications") if isinstance(p.get("medications"), list) else []
    if not g_meds:
        med_ok = len(p_meds) == 0  # 标注为空时，预测也不得多报
    else:
        used: set[int] = set()
        for gm in g_meds:
            idx = _match_med(gm, p_meds, used) if isinstance(gm, dict) else None
            if idx is None:
                break
            used.add(idx)
        med_ok = len(used) == len(g_meds) and len(p_meds) == len(g_meds)

    g_hist = gold.get("history") or []
    hist_ok = _match_list(g_hist, p.get("history") if isinstance(p.get("history"), list) else [])

    fields = {
        "patient.age": int(age_ok),
        "patient.sex": int(sex_ok),
        "chief_complaint": int(cc_ok),
        "vitals.bp": int(bp_ok),
        "vitals.heart_rate": int(hr_ok),
        "vitals.temperature": int(temp_ok),
        "diagnoses": int(diag_ok),
        "medications": int(med_ok),
        "history": int(hist_ok),
    }
    return {
        "fields": fields,
        "correct": sum(fields.values()),
        "total": len(fields),
    }


# 9 个叶子字段（评分口径的固定顺序，报告按此展示）
FIELD_NAMES = [
    "patient.age", "patient.sex", "chief_complaint", "vitals.bp",
    "vitals.heart_rate", "vitals.temperature",
    "diagnoses", "medications", "history",
]


def summarize(scored: list[dict]) -> dict:
    """汇总一批已评分记录：总体字段准确率 + 分字段准确率。"""
    per_field = {name: {"correct": 0, "total": 0} for name in FIELD_NAMES}
    total_correct = total_fields = 0
    for s in scored:
        for name, ok in s["fields"].items():
            per_field[name]["total"] += 1
            per_field[name]["correct"] += ok
        total_correct += s["correct"]
        total_fields += s["total"]
    return {
        "overall_accuracy": total_correct / total_fields if total_fields else 0.0,
        "total_correct": total_correct,
        "total_fields": total_fields,
        "per_field": {
            name: (v["correct"] / v["total"] if v["total"] else 0.0)
            for name, v in per_field.items()
        },
    }
