"""AI 病历治理引擎单元测试（PHI 脱敏 + 结构化清洗）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "services"))

from governance import (  # noqa: E402
    _clean_structured,
    deidentify,
    rule_structure,
)


def test_deidentify_id_card():
    text = "身份证 330302196503124421，请核对。"
    r = deidentify(text)
    assert "330302196503124421" not in r.masked_text
    assert any(e["masked"].startswith("330302") for e in r.entities)


def test_deidentify_phone_and_hospital_no():
    text = "联系方式 13812345678，住院号 ZY2026088。"
    r = deidentify(text)
    assert "13812345678" not in r.masked_text
    assert "ZY2026088" not in r.masked_text
    assert len(r.entities) >= 2


def test_deidentify_name_keeps_surname():
    text = "患者张阿姨，65岁，女。"
    r = deidentify(text)
    assert "张" in r.masked_text          # 姓氏保留
    assert "张阿姨" not in r.masked_text  # 全名被掩码


def test_deidentify_no_false_positive_on_clinical_text():
    text = "入院诊断：慢性心力衰竭急性加重。予以呋塞米注射液 40mg 静推。"
    r = deidentify(text)
    assert r.masked_text == text  # 纯临床文本不应被误掩码
    assert len(r.entities) == 0


def test_bp_clean_fixes_llm_typo():
    data = {"vitals": {"bp": "158/9:92", "heart_rate": 88}}
    cleaned = _clean_structured(data)
    assert cleaned["vitals"]["bp"] == "158/92"


def test_clean_structured_fills_missing_fields():
    cleaned = _clean_structured({"vitals": {"bp": "120/80"}})
    assert cleaned["patient"] is None
    assert cleaned["diagnoses"] == []
    assert cleaned["medications"] == []


def test_rule_structure_extracts_fields():
    note = ("患者李大爷，72岁，男。入院诊断：冠心病 心绞痛型，高血压2级。"
            "血压 146/88mmHg，心率 76次/分。")
    s = rule_structure(note)
    assert s["patient"]["age"] == 72
    assert s["patient"]["sex"] == "男"
    assert s["vitals"]["bp"] == "146/88"
    assert s["vitals"]["heart_rate"] == 76
    assert "冠心病 心绞痛型" in s["diagnoses"][0]
    assert "高血压2级" in s["diagnoses"]


def test_govern_full_pipeline_shape():
    from governance import govern

    note = "患者王女士，58岁，女。入院诊断：2型糖尿病。联系方式 13600001111。"
    result = govern(note, use_llm=False)
    assert len(result["deid"]["entities"]) >= 1
    assert "13600001111" not in result["deid"]["masked_text"]
    assert result["structured"]["patient"]["age"] == 58
    assert "PHI脱敏" in result["pipeline"][0]
