"""器官/身体部位分类表 — 前后端共同契约。

key 是未来 3D 模型网格名，label 是展示中文名，aliases 用于规则抽取与意图关键词。
成对部位：文本含"左/右"时归到 *_left / *_right；未说明左右时归到通用 key（如 shoulder），
绝不猜测左右。
"""

from __future__ import annotations

# key -> (label, aliases)
ORGANS: dict[str, tuple[str, list[str]]] = {
    "brain": ("头部/脑", ["头部", "头痛", "头晕", "偏头痛", "脑部", "脑子", "颅"]),
    "eyes": ("眼部", ["眼", "眼睛", "视力", "白内障", "眼底"]),
    "neck": ("颈部", ["颈部", "颈椎", "脖子", "甲状腺"]),
    "lungs": ("肺部", ["肺", "肺部", "结节", "肺结节", "胸片", "咳嗽", "支气管"]),
    "heart": ("心脏", ["心脏", "心悸", "冠心", "心电", "心律", "心肌", "胸闷"]),
    "liver": ("肝脏", ["肝", "肝脏", "脂肪肝", "转氨酶", "肝功"]),
    "stomach": ("胃部", ["胃", "胃部", "反酸", "胃镜", "胃炎", "胃痛"]),
    "kidneys": ("肾脏", ["肾", "肾脏", "肌酐", "尿蛋白", "肾功"]),
    "intestines": ("肠道", ["肠", "肠道", "腹泻", "便秘", "肠镜", "结肠", "肠胃"]),
    "spleen": ("脾脏", ["脾", "脾脏"]),
    "pancreas": ("胰腺", ["胰", "胰腺", "糖尿病"]),
    "spine": ("脊柱/腰背", ["腰", "腰椎", "背部", "后背", "脊柱", "椎间盘", "腰痛"]),
    "chest": ("胸部", ["胸部", "胸口", "乳腺", "肋骨"]),
    "abdomen": ("腹部", ["腹部", "肚子", "腹痛", "胆囊", "胰腺", "脾脏"]),
    "pelvis": ("盆腔", ["盆腔", "骨盆", "前列腺", "子宫", "膀胱"]),
    "uterus": ("子宫", ["子宫", "宫腔", "内膜"]),
    "ovaries": ("卵巢", ["卵巢", "输卵管"]),
    "prostate": ("前列腺", ["前列腺"]),
    "shoulder": ("肩部", ["肩", "肩部", "肩膀", "肩周"]),
    "shoulder_left": ("左肩", []),
    "shoulder_right": ("右肩", []),
    "arm": ("手臂", ["手臂", "胳膊", "手腕", "肘"]),
    "arm_left": ("左臂", []),
    "arm_right": ("右臂", []),
    "knee": ("膝盖", ["膝", "膝盖", "膝关节", "半月板"]),
    "knee_left": ("左膝", []),
    "knee_right": ("右膝", []),
    "leg": ("腿部", ["腿", "腿部", "小腿", "大腿", "脚踝", "足部", "脚"]),
    "leg_left": ("左腿", []),
    "leg_right": ("右腿", []),
}

# 有左右之分的通用 key
PAIRED = {"shoulder", "arm", "knee", "leg"}

# 症状/检查类信号词：用于判断一句话是否包含值得归档的健康信息
SYMPTOM_WORDS: list[str] = [
    "疼", "痛", "不适", "酸痛", "胀", "发麻", "肿", "结节", "囊肿", "息肉", "炎",
    "复查", "检查", "诊断", "查出", "确诊", "报告", "CT", "MRI", "核磁", "B超", "彩超",
    "手术", "住院", "就诊", "体检", "异常", "偏高", "偏低", "阳性", "阴性",
]

LABELS: dict[str, str] = {k: v[0] for k, v in ORGANS.items()}


def label_of(key: str) -> str:
    return LABELS.get(key, key)


def _side(text: str) -> str:
    if "左" in text and "右" not in text:
        return "_left"
    if "右" in text and "左" not in text:
        return "_right"
    return ""


def match_organs(text: str) -> list[str]:
    """按别名匹配文本中出现的器官 key（去重、保序）。

    成对部位带"左/右"时返回 *_left/*_right，否则返回通用 key。
    """
    found: list[str] = []
    for key, (_, aliases) in ORGANS.items():
        if not aliases or not any(a in text for a in aliases):
            continue
        resolved = key + _side(text) if key in PAIRED else key
        if resolved not in found:
            found.append(resolved)
    return found


def has_health_signal(text: str) -> bool:
    """是否包含值得归档的健康信息：出现器官别名 或 症状/检查词。"""
    return bool(match_organs(text)) or any(w in text for w in SYMPTOM_WORDS)
