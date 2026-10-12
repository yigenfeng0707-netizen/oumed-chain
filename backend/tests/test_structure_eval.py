"""P2.5 病历结构化评估：评分口径 + 评估集完整性（不依赖网络/LLM）"""

import json
from pathlib import Path

from app.services.structure_eval import FIELD_NAMES, score_record, summarize

DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "structure_eval_set.json"


def _gold() -> dict:
    return {
        "patient": {"age": 58, "sex": "男"},
        "chief_complaint": "反复头晕头痛3年，加重伴胸闷1周",
        "diagnoses": ["高血压3级", "冠心病"],
        "vitals": {"bp": "158/92", "heart_rate": 78, "temperature": 36.8},
        "medications": [{"name": "苯磺酸氨氯地平片", "dose": "5mg"}],
        "history": ["高血压病史10年"],
    }


def _perfect_pred() -> dict:
    gold = _gold()
    pred = json.loads(json.dumps(gold, ensure_ascii=False))
    pred["extractor"] = "llm:test"
    return pred


# ---------------- 评分口径 ----------------

def test_perfect_prediction_full_score():
    r = score_record(_perfect_pred(), _gold())
    assert r["correct"] == r["total"] == 9


def test_none_prediction_zero_score():
    r = score_record(None, _gold())
    assert r["correct"] == 0 and r["total"] == 9


def test_extractor_key_ignored():
    # pred 带 extractor 元数据字段不影响评分
    assert score_record(_perfect_pred(), _gold())["correct"] == 9


def test_null_gold_requires_null_pred():
    gold = _gold()
    gold["vitals"]["temperature"] = None
    pred = _perfect_pred()
    pred["vitals"]["temperature"] = None
    assert score_record(pred, gold)["correct"] == 9  # pred 同为 None → 正确
    pred["vitals"]["temperature"] = 36.5  # 标注空但预测多报 → 错
    assert score_record(pred, gold)["correct"] == 8


def test_bp_unit_and_format_tolerance():
    pred = _perfect_pred()
    pred["vitals"]["bp"] = "158/92mmHg"
    assert score_record(pred, _gold())["fields"]["vitals.bp"] == 1
    pred["vitals"]["bp"] = "160/94"
    assert score_record(pred, _gold())["fields"]["vitals.bp"] == 0


def test_text_fuzzy_and_space_tolerance():
    pred = _perfect_pred()
    pred["chief_complaint"] = "反复头晕头痛 3年，加重伴胸闷1周。"  # 空格/标点抖动
    assert score_record(pred, _gold())["fields"]["chief_complaint"] == 1
    pred["chief_complaint"] = "完全不同的主诉内容"
    assert score_record(pred, _gold())["fields"]["chief_complaint"] == 0


def test_medication_order_insensitive_with_dose():
    gold = _gold()
    gold["medications"] = [
        {"name": "阿托伐他汀钙片", "dose": "20mg"},
        {"name": "苯磺酸氨氯地平片", "dose": "5mg"},
    ]
    pred = _perfect_pred()
    pred["medications"] = [
        {"name": "苯磺酸氨氯地平片", "dose": "5mg 每日1次"},  # 剂量含频次子串可容错
        {"name": "阿托伐他汀钙片", "dose": "20mg"},
    ]
    assert score_record(pred, gold)["fields"]["medications"] == 1
    pred["medications"].append({"name": "阿司匹林肠溶片", "dose": "100mg"})  # 多报 → 错
    assert score_record(pred, gold)["fields"]["medications"] == 0


def test_empty_medications_requires_empty_pred():
    gold = _gold()
    gold["medications"] = []
    pred = _perfect_pred()
    pred["medications"] = []
    assert score_record(pred, gold)["fields"]["medications"] == 1
    pred["medications"] = [{"name": "某药", "dose": "1mg"}]
    assert score_record(pred, gold)["fields"]["medications"] == 0


def test_history_list_fuzzy_match():
    pred = _perfect_pred()
    pred["history"] = ["高血压病史 10年"]  # 空格抖动
    assert score_record(pred, _gold())["fields"]["history"] == 1
    pred["history"] = ["糖尿病病史10年"]
    assert score_record(pred, _gold())["fields"]["history"] == 0


def test_sex_normalized():
    pred = _perfect_pred()
    pred["patient"]["sex"] = "男性"
    assert score_record(pred, _gold())["fields"]["patient.sex"] == 1


def test_summarize_aggregates():
    scored = [score_record(_perfect_pred(), _gold()) for _ in range(3)]
    scored.append(score_record(None, _gold()))
    s = summarize(scored)
    assert s["total_fields"] == 36
    assert s["total_correct"] == 27
    assert abs(s["overall_accuracy"] - 0.75) < 1e-9
    assert set(s["per_field"]) == set(FIELD_NAMES)


# ---------------- 评估集完整性（标注忠实性 = 可验收的前提） ----------------

def test_dataset_exists_with_200_records():
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert data["meta"]["total"] == 200
    assert len(data["records"]) == 200
    ids = [r["id"] for r in data["records"]]
    assert len(set(ids)) == 200


def test_dataset_gold_structure_complete():
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    for r in data["records"]:
        g = r["gold"]
        assert set(g) >= {"patient", "chief_complaint", "diagnoses", "vitals",
                          "medications", "history"}, r["id"]
        assert set(g["vitals"]) >= {"bp", "heart_rate", "temperature"}, r["id"]
        assert set(g["patient"]) >= {"age", "sex"}, r["id"]


def test_dataset_gold_faithful_to_note():
    """每个标注值都必须能在病历原文中找到出处（防标注漂移）。"""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    for r in data["records"]:
        n, g = r["note"], r["gold"]
        assert g["patient"]["sex"] in n, r["id"]
        assert f"{g['patient']['age']}岁" in n, r["id"]
        assert g["chief_complaint"] in n, r["id"]
        for dx in g["diagnoses"]:
            assert dx in n, (r["id"], dx)
        bp = g["vitals"]["bp"]
        assert bp in n, (r["id"], bp)
        assert f"{g['vitals']['heart_rate']}次/分" in n, r["id"]
        t = g["vitals"]["temperature"]
        if t is None:
            assert "体温" not in n, r["id"]
        else:
            assert f"{t:.1f}℃" in n, r["id"]
        for m in g["medications"]:
            assert m["name"] in n and m["dose"] in n, (r["id"], m)
        for h in g["history"]:
            assert h in n, (r["id"], h)
        if not g["history"]:
            assert "既往史：无特殊" in n, r["id"]
        if not g["medications"]:
            assert "暂未予药物治疗" in n, r["id"]


def test_dataset_scores_self_consistently():
    """标注对标注自评分必须满分（评分口径自洽的锚定检验）。"""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    total = 0
    for r in data["records"]:
        total += score_record(r["gold"], r["gold"])["correct"]
    assert total == 200 * 9
