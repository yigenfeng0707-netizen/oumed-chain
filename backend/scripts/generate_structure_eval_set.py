"""生成病历结构化评估集（P2.5 生产化验收：200 条标注对照）。

固定随机种子合成 200 份非结构化入院记录，附字段级标注（与
governance.STRUCTURE_PROMPT 的 9 个叶子字段一一对应），写入
data/structure_eval_set.json（仓库根 data/，与其他数据资产同惯例）。

标注忠实性由测试保证（tests/test_structure_eval.py：每个标注值都能
在病历原文中找到对应原文）。可重复运行：同 seed 输出逐字节一致。

用法（在 backend 目录）：
    python scripts/generate_structure_eval_set.py [--output ../data/structure_eval_set.json]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SEED = 2026
TOTAL = 200

# ---------------- 病种模板池（模板字段与标注同源，保证标注忠实） ----------------

CONDITIONS = [
    {
        "dept": "心血管内科", "dx": ["高血压3级", "冠状动脉粥样硬化性心脏病"],
        "cc": ["反复头晕头痛{dur}年，加重伴胸闷{days}天", "间断心悸胸闷{dur}年，再发{days}天"],
        "bp": (150, 185), "hr": (70, 100), "temp": (36.2, 36.9),
        "meds": [("苯磺酸氨氯地平片", "5mg"), ("阿托伐他汀钙片", "20mg"), ("阿司匹林肠溶片", "100mg")],
        "hist_pool": ["高血压病史{hy}年", "2型糖尿病病史{hy}年", "高脂血症{hy}年"],
    },
    {
        "dept": "内分泌科", "dx": ["2型糖尿病", "糖尿病周围神经病变"],
        "cc": ["多饮多尿伴乏力{dur}年，血糖控制不佳{days}天", "口干多饮{dur}年，四肢麻木{days}天"],
        "bp": (115, 145), "hr": (68, 92), "temp": (36.1, 36.8),
        "meds": [("盐酸二甲双胍片", "0.5g"), ("格列美脲片", "2mg"), ("甲钴胺片", "0.5mg")],
        "hist_pool": ["2型糖尿病病史{hy}年", "高血压病史{hy}年"],
    },
    {
        "dept": "呼吸内科", "dx": ["社区获得性肺炎"],
        "cc": ["咳嗽咳痰{days}天，伴发热{d2}天", "发热伴气促{days}天"],
        "bp": (105, 135), "hr": (88, 118), "temp": (37.8, 39.4),
        "meds": [("注射用头孢曲松钠", "2g"), ("氨溴索口服液", "10ml"), ("对乙酰氨基酚片", "0.5g")],
        "hist_pool": ["慢性支气管炎{hy}年", "高血压病史{hy}年"],
    },
    {
        "dept": "消化内科", "dx": ["慢性浅表性胃炎", "反流性食管炎"],
        "cc": ["上腹胀痛{dur}月，反酸烧心{days}天", "间断上腹隐痛{dur}年，再发{days}天"],
        "bp": (108, 138), "hr": (66, 90), "temp": (36.2, 36.8),
        "meds": [("奥美拉唑肠溶胶囊", "20mg"), ("铝碳酸镁咀嚼片", "0.5g"), ("莫沙必利片", "5mg")],
        "hist_pool": ["慢性胃炎{hy}年", "胆囊结石{hy}年"],
    },
    {
        "dept": "神经内科", "dx": ["短暂性脑缺血发作", "脑动脉硬化症"],
        "cc": ["发作性右侧肢体无力{days}天", "反复眩晕伴恶心{dur}月，加重{days}天"],
        "bp": (125, 165), "hr": (64, 92), "temp": (36.3, 36.9),
        "meds": [("阿司匹林肠溶片", "100mg"), ("阿托伐他汀钙片", "20mg"), ("尼莫地平片", "30mg")],
        "hist_pool": ["高血压病史{hy}年", "心房颤动{hy}年", "2型糖尿病病史{hy}年"],
    },
    {
        "dept": "肾内科", "dx": ["慢性肾脏病3期", "肾性贫血"],
        "cc": ["发现血肌酐升高{dur}年，乏力加重{days}天", "双下肢水肿{dur}月，加重伴尿量减少{days}天"],
        "bp": (130, 168), "hr": (70, 96), "temp": (36.2, 36.7),
        "meds": [("复方α-酮酸片", "2.52g"), ("琥珀酸亚铁片", "0.1g"), ("硝苯地平控释片", "30mg")],
        "hist_pool": ["慢性肾炎{hy}年", "高血压病史{hy}年"],
    },
    {
        "dept": "骨科", "dx": ["腰椎间盘突出症", "骨质疏松症"],
        "cc": ["腰痛伴左下肢放射痛{dur}月，加重{days}天", "腰背部疼痛{dur}年，行走不利{days}天"],
        "bp": (112, 142), "hr": (66, 90), "temp": (36.1, 36.7),
        "meds": [("塞来昔布胶囊", "0.2g"), ("骨化三醇软胶囊", "0.25μg"), ("盐酸乙哌立松片", "50mg")],
        "hist_pool": ["腰椎间盘突出{hy}年", "高血压病史{hy}年"],
    },
    {
        "dept": "普外科", "dx": ["胆囊结石伴慢性胆囊炎"],
        "cc": ["右上腹疼痛{dur}月，再发{days}天", "间歇性右上腹隐痛{dur}年，加重伴腹胀{days}天"],
        "bp": (110, 140), "hr": (70, 95), "temp": (36.4, 37.6),
        "meds": [("头孢克肟分散片", "50mg"), ("消炎利胆片", "0.5g"), ("山莨菪碱片", "5mg")],
        "hist_pool": ["胆囊结石{hy}年", "2型糖尿病病史{hy}年"],
    },
    {
        "dept": "心内科", "dx": ["慢性心力衰竭", "心房颤动"],
        "cc": ["活动后气促{dur}年，加重伴双下肢水肿{days}天", "心悸气短{dur}月，夜间不能平卧{days}天"],
        "bp": (118, 152), "hr": (85, 120), "temp": (36.2, 36.8),
        "meds": [("地高辛片", "0.125mg"), ("呋塞米片", "20mg"), ("螺内酯片", "20mg")],
        "hist_pool": ["冠心病{hy}年", "高血压病史{hy}年", "心房颤动{hy}年"],
    },
    {
        "dept": "老年医学科", "dx": ["冠状动脉粥样硬化性心脏病", "稳定型心绞痛"],
        "cc": ["胸闷胸痛{dur}年，发作频率增加{days}天", "劳力性心前区疼痛{dur}年，再发{days}天"],
        "bp": (120, 158), "hr": (60, 88), "temp": (36.2, 36.7),
        "meds": [("单硝酸异山梨酯缓释片", "40mg"), ("阿托伐他汀钙片", "20mg"), ("氯吡格雷片", "75mg")],
        "hist_pool": ["冠心病{hy}年", "高血压病史{hy}年"],
    },
    {
        "dept": "内分泌科", "dx": ["甲状腺功能亢进症"],
        "cc": ["心悸多汗伴体重下降{dur}月", "怕热多汗、手抖{dur}月，加重{days}天"],
        "bp": (112, 145), "hr": (92, 128), "temp": (36.4, 37.2),
        "meds": [("甲巯咪唑片", "10mg"), ("普萘洛尔片", "10mg")],
        "hist_pool": ["甲状腺功能亢进{hy}年"],
    },
    {
        "dept": "呼吸内科", "dx": ["慢性阻塞性肺疾病急性加重期"],
        "cc": ["反复咳喘{dur}年，加重伴气促{days}天", "慢性咳嗽咳痰{dur}年，痰量增多{days}天"],
        "bp": (115, 148), "hr": (86, 115), "temp": (36.5, 38.2),
        "meds": [("噻托溴铵粉吸入剂", "18μg"), ("多索茶碱片", "0.2g"), ("头孢呋辛酯片", "0.25g")],
        "hist_pool": ["慢阻肺{hy}年", "吸烟史30年", "高血压病史{hy}年"],
    },
]

_SURNAMES = ["王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴", "徐", "孙", "林", "何", "郑"]

_MED_FREQ = ["每日1次", "每日2次", "每日3次", "每晚1次"]
_HY_DAYS = {"dur_year": (1, 15), "days": (3, 20), "d2": (1, 5), "hy": (2, 20)}


def _fmt_num(x: float) -> str:
    """体温统一保留一位小数（36.5℃ / 39.0℃），保证标注值与原文可精确对照。"""
    return f"{x:.1f}"


def _render(template: str, rng: random.Random) -> str:
    return template.format(
        dur=rng.randint(*_HY_DAYS["dur_year"]),
        days=rng.randint(*_HY_DAYS["days"]),
        d2=rng.randint(*_HY_DAYS["d2"]),
        hy=rng.randint(*_HY_DAYS["hy"]),
    )


def _build_record(idx: int, rng: random.Random) -> dict:
    cond = CONDITIONS[idx % len(CONDITIONS)]
    sex = rng.choice(["男", "女"])
    age = rng.randint(35, 82)
    # 称谓与性别一致（男：先生/某/某某，女：女士/某/某某），避免“张女士…男性”式语义矛盾
    title = rng.choice(["先生", "某", "某某"] if sex == "男" else ["女士", "某", "某某"])
    name = rng.choice(_SURNAMES) + title

    cc = _render(rng.choice(cond["cc"]), rng)
    duration = rng.randint(*_HY_DAYS["dur_year"])
    days = rng.randint(*_HY_DAYS["days"])
    bp_sys = rng.randint(*cond["bp"])
    bp_dia = bp_sys - rng.randint(30, 60)
    hr = rng.randint(*cond["hr"])
    temp = round(rng.uniform(*cond["temp"]), 1)
    has_temp = rng.random() > 0.15  # 15% 缺体温，考察空值处理
    has_hist = rng.random() > 0.12  # 12% 无特殊既往史，考察空列表处理
    has_meds = rng.random() > 0.08  # 8% 暂未用药，考察空列表处理

    n_meds = len(cond["meds"]) if has_meds else 0
    meds_pool = cond["meds"][:]
    rng.shuffle(meds_pool)
    meds = meds_pool[: max(1, min(n_meds, rng.randint(1, 3)))] if has_meds else []
    hist = []
    if has_hist:
        hist_pool = cond["hist_pool"][:]
        rng.shuffle(hist_pool)
        hist = [_render(h, rng) for h in hist_pool[: rng.randint(1, 2)]]

    # ---- 组装病历原文（表述即标注来源，保证标注忠实） ----
    vital_items = []
    if has_temp:
        vital_items.append(f"体温{_fmt_num(temp)}℃")
    vital_items.append(f"脉搏{hr}次/分")
    vital_items.append(f"血压{bp_sys}/{bp_dia}mmHg")
    vitals_line = "查体：" + "，".join(vital_items)

    dx_lines = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(cond["dx"]))
    hist_line = "、".join(hist) if hist else "无特殊"
    if meds:
        med_line = "；".join(
            f"{m[0]} {m[1]} {rng.choice(_MED_FREQ)}" for m in meds
        )
        plan_line = f"诊疗计划：给予{med_line}，完善相关检查。"
    else:
        plan_line = "诊疗计划：暂未予药物治疗，完善相关检查后制定方案。"

    note = (
        f"入院记录\n"
        f"患者{name}，{age}岁，{sex}性，因“{cc}”入院。\n"
        f"住院号：ZY2026{idx:04d}，联系电话：138{rng.randint(10000000, 99999999):08d}。\n"
        f"主诉：{cc}。\n"
        f"现病史：患者于{duration}年前起病，近期症状加重{days}天，为求进一步诊治入院。\n"
        f"既往史：{hist_line}。\n"
        f"{vitals_line}。\n"
        f"辅助检查：完善血常规、生化、心电图等检查（略）。\n"
        f"入院诊断：\n{dx_lines}\n"
        f"{plan_line}"
    )

    gold = {
        "patient": {"age": age, "sex": sex},
        "chief_complaint": cc,
        "diagnoses": list(cond["dx"]),
        "vitals": {
            "bp": f"{bp_sys}/{bp_dia}",
            "heart_rate": hr,
            "temperature": (temp if has_temp else None),
        },
        "medications": [{"name": m[0], "dose": m[1]} for m in meds],
        "history": hist,
    }
    return {"id": f"eval-{idx:03d}", "note": note, "gold": gold}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="../data/structure_eval_set.json")
    args = ap.parse_args()

    rng = random.Random(SEED)
    records = [_build_record(i, rng) for i in range(TOTAL)]

    out = {
        "meta": {
            "seed": SEED,
            "total": TOTAL,
            "purpose": "P2.5 病历结构化字段级评估（对照 governance.llm_structure）",
            "fields": ["patient.age", "patient.sex", "chief_complaint",
                       "vitals.bp", "vitals.heart_rate", "vitals.temperature",
                       "diagnoses", "medications", "history"],
        },
        "records": records,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已生成 {TOTAL} 条评估集 -> {path.resolve()}")


if __name__ == "__main__":
    main()
