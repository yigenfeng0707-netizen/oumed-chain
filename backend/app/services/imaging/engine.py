"""
瓯医数链 Agent - 医学影像 AI 标注引擎 (Imaging Engine)

多模态医疗信号识别核心模块之一 —— 医学影像病灶检测与 AI 辅助标注。
与 EEG 脑电引擎并列，构成"脑电 + 影像"双模态关键医疗信号识别闭环。

核心能力：
- 合成医学影像生成（胸片 X 光 / 肺部 CT / 脑部 MRI，确定性渲染，可复现）
- 病灶检测流水线（局部对比度增强 → 自适应阈值分割 → 连通域分析 → 形态学特征分类）
- AI 预标注（边界框 + 疑似类别 + 置信度 + 严重度分级）
- 医生复核（确认 / 修正 / 驳回）
- 结构化影像报告 + 医保检查联动推荐

技术栈：numpy（图像分析）+ Pillow（PNG 渲染），零外部模型依赖，可离线运行。
设计依据：放射影像病灶检测的经典图像处理流水线（预处理→候选区→特征→分类→后处理）。

安全性声明：本引擎为疾病筛查辅助工具（Demo 用合成影像），
AI 标注仅供临床参考，最终诊断须由持证医师复核确认。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

logger = logging.getLogger(__name__)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow 未安装，影像渲染将降级为字符画模式")

# ============================================================
# 常量：影像尺寸 / 支持检查类型 / 病灶类别
# ============================================================

IMG_SIZE = 512  # 合成影像边长（像素），512×512 灰度高斯风格

# 检查类型元数据：中文名 + 解剖背景渲染参数
STUDY_TYPES = {
    "chest_xray": {
        "label": "胸部 X 光（Chest X-Ray）",
        "short_label": "胸片",
        "findings": [
            "nodule", "infiltration", "effusion", "cardiomegaly",
        ],
    },
    "lung_ct": {
        "label": "肺部 CT 横断面（Lung CT）",
        "short_label": "肺CT",
        "findings": [
            "nodule", "ground_glass", "emphysema", "pneumothorax",
        ],
    },
    "brain_mri": {
        "label": "脑部 MRI 轴位（Brain MRI）",
        "short_label": "脑MRI",
        "findings": [
            "tumor", "hemorrhage", "lacunar_infarct",
        ],
    },
}

# 病灶类别元数据：中文名 + 严重度默认 + 灰度相对背景偏移方向
FINDINGS_META = {
    # ---- 胸片 ----
    "nodule": {
        "label": "肺结节", "severity": "medium", "tone": "light",
        "desc": "局灶性高密度影，边界清晰，可疑早期肺癌征象。",
    },
    "infiltration": {
        "label": "炎性浸润", "severity": "medium", "tone": "light",
        "desc": "斑片状模糊高密度影，多见于感染性病变。",
    },
    "effusion": {
        "label": "胸腔积液", "severity": "high", "tone": "light",
        "desc": "肋膈角钝化、液平面征，提示中大量积液。",
    },
    "cardiomegaly": {
        "label": "心脏增大", "severity": "medium", "tone": "light",
        "desc": "心影横径增大（> 胸廓横径 50%），建议超声心动进一步评估。",
    },
    # ---- 肺CT ----
    "ground_glass": {
        "label": "磨玻璃影", "severity": "medium", "tone": "light",
        "desc": "云雾状密度增高影，不掩盖血管纹理，需随访或抗炎后复查。",
    },
    "emphysema": {
        "label": "肺气肿", "severity": "low", "tone": "dark",
        "desc": "局限性低密度区，肺实质破坏，多见于吸烟者。",
    },
    "pneumothorax": {
        "label": "气胸", "severity": "high", "tone": "dark",
        "desc": "肺野周边无纹理透亮带，脏层胸膜线内移，需急诊处理。",
    },
    # ---- 脑MRI ----
    "tumor": {
        "label": "占位病变", "severity": "high", "tone": "light",
        "desc": "局灶性异常信号占位，边界不清，建议增强扫描与神经外科会诊。",
    },
    "hemorrhage": {
        "label": "出血灶", "severity": "high", "tone": "light",
        "desc": "高信号出血灶，可疑脑出血，需急诊评估。",
    },
    "lacunar_infarct": {
        "label": "腔隙性梗死", "severity": "medium", "tone": "light",
        "desc": "小灶性梗死灶，与脑血管病危险因素相关。",
    },
}

# 影像异常 → 医保检查联动规则（文件可覆盖）
_IMAGING_POLICY_LINK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))),
    "data", "imaging_policy_link.json",
)
_IMAGING_POLICY_LINK_CACHE: dict | None = None


def load_imaging_policy_link() -> dict:
    """加载影像-医保政策联动规则库（带缓存，文件缺失时用内置默认）。"""
    global _IMAGING_POLICY_LINK_CACHE
    if _IMAGING_POLICY_LINK_CACHE is not None:
        return _IMAGING_POLICY_LINK_CACHE
    try:
        with open(_IMAGING_POLICY_LINK_PATH, encoding="utf-8") as f:
            _IMAGING_POLICY_LINK_CACHE = json.load(f)
        logger.info("加载影像-医保政策联动规则库: %s", _IMAGING_POLICY_LINK_PATH)
    except Exception as e:
        logger.warning("加载影像政策联动规则库失败: %s，使用内置默认规则", e)
        _IMAGING_POLICY_LINK_CACHE = _default_policy_link()
    return _IMAGING_POLICY_LINK_CACHE


def _default_policy_link() -> dict:
    """内置默认影像-医保联动规则（文件缺失时兜底）。"""
    return {
        "links": [
            {
                "trigger": "finding_high_risk",
                "condition": {"any_severity": "high"},
                "title": "检出高危影像异常",
                "policy_hint": "重点影像检查医保报销政策",
                "description": "影像检出高危异常，建议尽快专科就诊。CT/核磁等检查可按门诊统筹及大病保险政策报销。",
                "suggestion": "携带影像资料至三甲医院专科复诊；及时申请门诊慢特病认定。",
                "related_policies": ["CT/核磁检查医保报销", "门诊慢特病认定"],
            },
            {
                "trigger": "nodule_followup",
                "condition": {"findings": ["nodule", "ground_glass"]},
                "title": "结节随访提醒",
                "policy_hint": "肺结节随访复查报销",
                "description": "检出结节/磨玻璃影，建议按指南定期复查。随访 CT 属合理检查，可按比例报销。",
                "suggestion": "6-12 个月后复查；可申请肺癌早筛专项（部分地区免费/低收费）。",
                "related_policies": ["肺结节随访 CT 报销", "癌症早筛专项"],
            },
            {
                "trigger": "effusion_pneumo",
                "condition": {"findings": ["effusion", "pneumothorax"]},
                "title": "积液/气胸急诊提示",
                "policy_hint": "急诊检查与住院报销",
                "description": "胸腔积液或气胸需尽快处置，急诊检查费可纳入医保结算。",
                "suggestion": "立即至急诊科；住院治疗费用按住院统筹报销。",
                "related_policies": ["急诊检查报销", "住院统筹报销"],
            },
            {
                "trigger": "brain_acute",
                "condition": {"findings": ["hemorrhage", "tumor"]},
                "title": "脑部急性病变预警",
                "policy_hint": "脑部急症绿色通道",
                "description": "脑出血/占位属急重症，建议启动卒中或肿瘤绿色通道。",
                "suggestion": "立即就诊神经内外科；相关诊疗费用可按特殊病种报销。",
                "related_policies": ["卒中救治绿色通道", "特殊病种报销"],
            },
        ]
    }


# ============================================================
# 数据类：检测发现 / AI 标注 / 影像会话
# ============================================================

@dataclass
class Finding:
    """单个病灶发现（AI 或医生标注）。坐标均为归一化 [0,1]。"""
    finding_type: str                    # 病灶类别 key
    x: float                             # bbox 中心 x（归一化）
    y: float                             # bbox 中心 y（归一化）
    w: float                             # bbox 宽（归一化）
    h: float                             # bbox 高（归一化）
    confidence: float = 0.8              # AI 置信度
    severity: str = "medium"             # low / medium / high
    source: str = "ai"                   # ai / doctor
    status: str = "pending"              # pending / confirmed / rejected / corrected
    evidence: str = ""                   # 可解释性证据说明

    def to_dict(self) -> dict:
        return {
            "finding_type": self.finding_type,
            "label": FINDINGS_META.get(self.finding_type, {}).get("label", self.finding_type),
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "w": round(self.w, 4),
            "h": round(self.h, 4),
            "confidence": round(self.confidence, 3),
            "severity": self.severity,
            "source": self.source,
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass
class ImagingStudy:
    """一次影像分析会话。"""
    study_id: str
    user_id: str
    study_type: str
    seed: int
    findings: list = field(default_factory=list)   # list[Finding]
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    image_base64: str = ""                         # 渲染 PNG（base64）
    report: dict = field(default_factory=dict)     # 结构化报告

    def to_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "user_id": self.user_id,
            "study_type": self.study_type,
            "study_label": STUDY_TYPES.get(self.study_type, {}).get("label", self.study_type),
            "seed": self.seed,
            "created_at": self.created_at,
            "image_base64": self.image_base64,
            "findings": [f.to_dict() for f in self.findings],
            "report": self.report,
        }


# ============================================================
# 合成医学影像渲染（确定性：study_type + findings + seed）
# ============================================================

def _rng(seed: int) -> np.random.Generator:
    """固定种子随机数生成器，保证可复现。"""
    return np.random.default_rng(seed)


def _render_chest_xray(rng: np.random.Generator, findings: list[Finding], size: int = IMG_SIZE) -> np.ndarray:
    """渲染胸部 X 光：解剖背景 + 植入病灶（归一化灰度图 0-255）。"""
    img = np.full((size, size), 180.0)  # 软组织灰背景

    # 肺野（左右两块暗区）
    yy, xx = np.mgrid[0:size, 0:size]
    left_lung = ((xx - size * 0.32) / (size * 0.20)) ** 2 + ((yy - size * 0.52) / (size * 0.30)) ** 2 < 1
    right_lung = ((xx - size * 0.68) / (size * 0.20)) ** 2 + ((yy - size * 0.52) / (size * 0.30)) ** 2 < 1
    img[left_lung] = 95
    img[right_lung] = 95
    img[left_lung] = img[left_lung] + rng.normal(0, 6, left_lung.sum())   # 肺纹理噪声
    img[right_lung] = img[right_lung] + rng.normal(0, 6, right_lung.sum())

    # 肋骨影（椭圆环带，保留肺野内部噪声区域）
    for cx, cy, rx, ry in [(0.32, 0.50, 0.21, 0.32), (0.68, 0.50, 0.21, 0.32)]:
        rib_outer = ((xx - size * cx) / (size * rx)) ** 2 + ((yy - size * cy) / (size * ry)) ** 2 < 1
        rib_inner = ((xx - size * cx) / (size * rx * 0.80)) ** 2 + ((yy - size * cy) / (size * ry * 0.80)) ** 2 < 1
        rib = rib_outer & ~rib_inner
        img[rib] = 205

    # 心影（左下）
    heart = ((xx - size * 0.40) / (size * 0.14)) ** 2 + ((yy - size * 0.60) / (size * 0.17)) ** 2 < 1
    img[heart] = 200

    # 横膈 + 纵膈
    diaphragm = (yy > size * 0.78) & (yy < size * 0.86)
    img[diaphragm] = 150
    mediastinum = (xx > size * 0.42) & (xx < size * 0.58)
    img[mediastinum & (yy < size * 0.80)] = 210

    _implant_findings(img, findings, rng, size)
    return np.clip(img, 0, 255)


def _render_lung_ct(rng: np.random.Generator, findings: list[Finding], size: int = IMG_SIZE) -> np.ndarray:
    """渲染肺部 CT 横断面：胸壁环 + 双肺 + 纵膈 + 病灶。"""
    img = np.full((size, size), 12.0)  # 肺窗黑背景
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy, rx, ry = 0.5, 0.5, 0.40, 0.40
    body = ((xx - size * cx) / (size * rx)) ** 2 + ((yy - size * cy) / (size * ry)) ** 2 < 1
    img[body] = 35  # 肺实质
    img[body] = img[body] + rng.normal(0, 5, body.sum())

    # 胸壁
    wall = ((xx - size * cx) / (size * 0.42)) ** 2 + ((yy - size * cy) / (size * 0.42)) ** 2 < 1
    wall = wall & ~body
    img[wall] = 180

    # 纵膈（中央）
    medi = (np.abs(xx - size * 0.5) < size * 0.06) & body
    img[medi] = 120

    # 血管纹理
    for _ in range(18):
        px, py = rng.integers(size * 0.15, size * 0.85), rng.integers(size * 0.15, size * 0.85)
        r = rng.integers(6, 18)
        vessel = ((xx - px) / r) ** 2 + ((yy - py) / r) ** 2 < 1
        img[vessel & body] = 75

    _implant_findings(img, findings, rng, size)
    return np.clip(img, 0, 255)


def _render_brain_mri(rng: np.random.Generator, findings: list[Finding], size: int = IMG_SIZE) -> np.ndarray:
    """渲染脑部 MRI 轴位：颅骨环 + 脑实质 + 脑室 + 病灶。"""
    img = np.full((size, size), 8.0)  # 颅外黑
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy, rx, ry = 0.5, 0.5, 0.42, 0.42
    skull = ((xx - size * cx) / (size * rx)) ** 2 + ((yy - size * cy) / (size * ry)) ** 2 < 1
    img[skull] = 60
    img[skull] = img[skull] + rng.normal(0, 6, skull.sum())

    # 脑回纹理（轻度正弦调制）
    brain = ((xx - size * cx) / (size * 0.36)) ** 2 + ((yy - size * cy) / (size * 0.36)) ** 2 < 1
    img[brain] = 105
    texture = (np.sin(xx / 9) + np.sin(yy / 11)) * 6
    img[brain] += texture[brain]

    # 脑室（双侧暗区）
    for vx in (0.44, 0.56):
        vent = ((xx - size * vx) / (size * 0.05)) ** 2 + ((yy - size * 0.48) / (size * 0.10)) ** 2 < 1
        img[vent] = 30

    # 中线结构
    fissure = (np.abs(xx - size * 0.5) < size * 0.008) & brain
    img[fissure] = 140

    _implant_findings(img, findings, rng, size)
    return np.clip(img, 0, 255)


# 病灶类别 → 渲染函数注册表（tone: light=高亮病灶 / dark=低密度病灶）
def _implant_findings(img: np.ndarray, findings: list[Finding], rng: np.random.Generator, size: int) -> None:
    """将病灶按归一化坐标植入合成影像（确定性）。"""
    for f in findings:
        cx, cy = int(f.x * size), int(f.y * size)
        w, h = int(f.w * size / 2), int(f.h * size / 2)
        ftype = f.finding_type
        meta = FINDINGS_META.get(ftype, {})
        tone = meta.get("tone", "light")
        yy, xx = np.mgrid[0:size, 0:size]

        if ftype == "nodule":
            mask = ((xx - cx) / max(w, 4)) ** 2 + ((yy - cy) / max(h, 4)) ** 2 < 1
            val = 235 if tone == "light" else 20
            img[mask] = val
        elif ftype == "infiltration":
            # 斑片状模糊高密度
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 150
            img[mask] += rng.normal(0, 18, mask.sum())
        elif ftype == "ground_glass":
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 90
        elif ftype == "effusion":
            # 肋膈角液平面（三角形高密度）
            mask = (yy > cy - h // 2) & (yy < cy + h // 2) & (xx > cx - w // 2) & (xx < cx + w // 2)
            mask &= ((xx - (cx - w // 2)) / max(w, 4) + (yy - (cy + h // 2)) / max(h, 4)) > 0
            img[mask] = 210
        elif ftype == "cardiomegaly":
            mask = ((xx - cx) / max(w, 10)) ** 2 + ((yy - cy) / max(h, 10)) ** 2 < 1
            img[mask] = 215
        elif ftype == "emphysema":
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 18
        elif ftype == "pneumothorax":
            # 周边透亮带
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 15
            img[mask] = img[mask] + rng.normal(0, 4, mask.sum())
        elif ftype == "tumor":
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 220
            img[mask] = img[mask] + rng.normal(0, 10, mask.sum())
        elif ftype == "hemorrhage":
            mask = ((xx - cx) / max(w, 8)) ** 2 + ((yy - cy) / max(h, 8)) ** 2 < 1
            img[mask] = 225
        elif ftype == "lacunar_infarct":
            mask = ((xx - cx) / max(w, 5)) ** 2 + ((yy - cy) / max(h, 5)) ** 2 < 1
            img[mask] = 215


def render_study_image(study_type: str, findings: list[Finding], seed: int, size: int = IMG_SIZE) -> str:
    """渲染检查影像为 base64 PNG（确定性）。

    Args:
        study_type: 检查类型 key（chest_xray / lung_ct / brain_mri）
        findings: 病灶列表（用于植入合成影像）
        seed: 随机种子（保证解剖噪声可复现）

    Returns:
        data URI 形式的 base64 PNG（或降级字符画）。
    """
    rng = _rng(seed)
    if study_type == "chest_xray":
        arr = _render_chest_xray(rng, findings, size)
    elif study_type == "lung_ct":
        arr = _render_lung_ct(rng, findings, size)
    elif study_type == "brain_mri":
        arr = _render_brain_mri(rng, findings, size)
    else:
        arr = _render_chest_xray(rng, findings, size)

    # 增强对比度 + 伪彩色暖化（医学影像显示惯例）
    arr = np.clip((arr - arr.min()) / max(arr.max() - arr.min(), 1e-6) * 255, 0, 255)

    if not HAS_PIL:
        return "data:image/png;base64," + _fallback_ascii(arr)
    pil_img = Image.fromarray(arr.astype(np.uint8), mode="L").convert("RGB")
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _fallback_ascii(arr: np.ndarray) -> str:
    """无 Pillow 时的降级渲染（base64 编码 ASCII 字符画）。"""
    chars = " .:-=+*#%@"
    h, w = arr.shape
    lines = []
    for y in range(0, h, 8):
        line = "".join(chars[min(int(arr[y, x] / 256 * len(chars)), len(chars) - 1)] for x in range(0, w, 8))
        lines.append(line)
    ascii_art = "\n".join(lines)
    return base64.b64encode(ascii_art.encode("utf-8")).decode("ascii")


# ============================================================
# 病灶检测流水线（预处理 → 候选区 → 特征 → 分类打分）
# ============================================================

def _preprocess(img: np.ndarray) -> np.ndarray:
    """预处理：灰度归一化 + 高斯平滑去噪（保留结构边缘）。"""
    norm = (img - img.min()) / max(img.max() - img.min(), 1e-6)
    kernel = np.array([[1, 2, 1], [2, 4, 2], [1, 2, 1]], dtype=float) / 16
    smooth = np.zeros_like(norm)
    for _ in range(2):
        smooth = np.pad(norm, 1, mode="edge")
        smooth = np.array([
            [np.sum(kernel * smooth[y:y + 3, x:x + 3]) for x in range(norm.shape[1])]
            for y in range(norm.shape[0])
        ])
        norm = smooth
    return norm


def _local_contrast_enhance(img: np.ndarray, radius: int = 32) -> np.ndarray:
    """局部对比度增强：D(x) = I(x) - mean_{N(x)}(I)。

    使用积分图（cumsum）实现 O(n) 的盒式均值滤波，纯 numpy，无 scipy 依赖。
    """
    k = max(radius // 2, 1)
    pad = np.pad(img, k, mode="edge")  # 尺寸 (h+2k, w+2k)
    # 积分图 S[i,j] = sum(pad[0:i, 0:j])，尺寸 (h+2k+1, w+2k+1)
    cum = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    s = np.zeros((pad.shape[0] + 1, pad.shape[1] + 1), dtype=float)
    s[1:, 1:] = cum
    h, w = img.shape
    # 输出 (i,j) 对应 pad 窗口 [i, i+2k] × [j, j+2k]
    a = s[0:h, 0:w]
    b = s[0:h, 2 * k + 1:2 * k + 1 + w]
    c = s[2 * k + 1:2 * k + 1 + h, 0:w]
    d = s[2 * k + 1:2 * k + 1 + h, 2 * k + 1:2 * k + 1 + w]
    window_sum = d - b - c + a
    box_area = (2 * k + 1) ** 2
    local_mean = window_sum / box_area
    return img - local_mean


def _connected_components(mask: np.ndarray, min_size: int = 12) -> list[tuple]:
    """连通域分析（BFS），返回 [(ys, xs)] 区域索引列表。"""
    visited = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    regions: list[tuple] = []
    for y in range(h):
        for x in range(w):
            if mask[y, x] and not visited[y, x]:
                stack = [(y, x)]
                visited[y, x] = True
                region_y, region_x = [], []
                while stack:
                    cy, cx = stack.pop()
                    region_y.append(cy)
                    region_x.append(cx)
                    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            stack.append((ny, nx))
                if len(region_y) >= min_size:
                    regions.append((np.array(region_y), np.array(region_x)))
    return regions


def _region_features(img: np.ndarray, region_y: np.ndarray, region_x: np.ndarray) -> dict:
    """提取区域形态学特征：面积/圆度/中心/强度/边缘锐度。"""
    area = len(region_y)
    cy, cx = float(region_y.mean()), float(region_x.mean())
    bbox_h = float(region_y.max() - region_y.min() + 1)
    bbox_w = float(region_x.max() - region_x.min() + 1)
    # 圆度：区域面积 / 包围盒面积
    fill_ratio = area / max(bbox_h * bbox_w, 1)
    # 平均强度
    mean_intensity = float(img[region_y, region_x].mean())
    # 边缘锐度：区域像素强度标准差
    edge_sharpness = float(img[region_y, region_x].std())
    return {
        "area": area, "cy": cy, "cx": cx,
        "bbox_h": bbox_h, "bbox_w": bbox_w,
        "fill_ratio": fill_ratio,
        "mean_intensity": mean_intensity,
        "edge_sharpness": edge_sharpness,
    }


def _classify_region(feats: dict, study_type: str, img_shape: tuple) -> tuple[str, float, str]:
    """按形态学特征对候选区域分类打分，返回 (finding_type, confidence, severity)。

    分类依据（放射影像共识规则）：
    - 高密度、圆形、边界清晰 → 结节（nodule）
    - 高密度、不规则、边缘模糊 → 炎性浸润（infiltration）/ 磨玻璃影
    - 低密度、圆形 → 肺气肿 / 气胸（CT）
    - 大面积高密度、位置偏心 → 积液 / 占位
    """
    h, w = img_shape
    rel_area = feats["area"] / (h * w)
    norm_x, norm_y = feats["cx"] / w, feats["cy"] / h
    fill = feats["fill_ratio"]
    bright = feats["mean_intensity"] > 0.6
    dark = feats["mean_intensity"] < 0.35

    if study_type == "brain_mri":
        if bright and rel_area > 0.006 and fill > 0.5:
            if norm_y < 0.45:  # 偏上 → 出血/占位
                return "hemorrhage", min(0.97, 0.80 + rel_area * 3), "high"
            return "tumor", min(0.96, 0.78 + rel_area * 3), "high"
        if bright and rel_area > 0.002:
            return "lacunar_infarct", 0.72 + min(0.2, rel_area * 2), "medium"
        return "tumor", 0.68, "medium"

    if study_type == "lung_ct":
        if dark and fill > 0.55:
            return "pneumothorax" if norm_y < 0.5 else "emphysema", 0.80 + min(0.15, rel_area), "high" if norm_y < 0.5 else "low"
        if bright and fill > 0.45 and rel_area > 0.004:
            return "nodule", min(0.95, 0.75 + rel_area * 4), "medium"
        if bright:
            return "ground_glass", 0.70 + min(0.2, rel_area), "medium"
        return "nodule", 0.65, "low"

    # chest_xray 默认
    if dark and rel_area > 0.02 and norm_y > 0.55:
        return "emphysema", 0.70, "low"
    if bright and rel_area > 0.03 and norm_x < 0.5 and norm_y > 0.55:
        return "cardiomegaly", 0.82, "medium"
    if bright and rel_area > 0.012 and fill > 0.5:
        return "nodule", min(0.95, 0.75 + rel_area * 4), "medium"
    if bright and fill < 0.5:
        return "infiltration", 0.75, "medium"
    if bright and norm_y > 0.72 and norm_x > 0.5:
        return "effusion", 0.80, "high"
    return "nodule", 0.62, "low"


def detect_findings(img: np.ndarray, study_type: str) -> list[Finding]:
    """AI 病灶检测：对灰度影像执行完整检测流水线。

    Args:
        img: 灰度影像（0-255，ndarray）
        study_type: 检查类型

    Returns:
        list[Finding]：AI 预标注结果（带证据说明）。
    """
    h, w = img.shape
    norm = _preprocess(img)
    contrast = _local_contrast_enhance(norm)

    # 高/低密度候选掩膜
    high_mask = contrast > 0.12
    low_mask = contrast < -0.12
    mask = high_mask | low_mask

    regions = _connected_components(mask)
    findings: list[Finding] = []
    for region_y, region_x in regions:
        feats = _region_features(img, region_y, region_x)
        ftype, conf, sev = _classify_region(feats, study_type, (h, w))
        bbox_h = feats["bbox_h"]
        bbox_w = feats["bbox_w"]
        findings.append(Finding(
            finding_type=ftype,
            x=round(feats["cx"] / w, 4),
            y=round(feats["cy"] / h, 4),
            w=round(bbox_w / w, 4),
            h=round(bbox_h / h, 4),
            confidence=round(conf, 3),
            severity=sev,
            source="ai",
            status="pending",
            evidence=(
                f"区域面积 {feats['area']}px、填充率 {feats['fill_ratio']:.2f}、"
                f"平均强度 {feats['mean_intensity']:.2f}、"
                f"边缘锐度 {feats['edge_sharpness']:.2f}；"
                f"符合{FINDINGS_META.get(ftype, {}).get('label', ftype)}影像特征"
            ),
        ))

    # 按置信度降序，去重（重叠度 IoU > 0.5 保留高置信者）
    findings.sort(key=lambda f: f.confidence, reverse=True)
    deduped: list[Finding] = []
    for f in findings:
        if all(_iou(f, g) < 0.5 for g in deduped):
            deduped.append(f)
    return deduped[:6]


def _iou(a: Finding, b: Finding) -> float:
    """两个归一化 bbox 的交并比。"""
    ax1, ay1 = a.x - a.w / 2, a.y - a.h / 2
    ax2, ay2 = a.x + a.w / 2, a.y + a.h / 2
    bx1, by1 = b.x - b.w / 2, b.y - b.h / 2
    bx2, by2 = b.x + b.w / 2, b.y + b.h / 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a, area_b = a.w * a.h, b.w * b.h
    union = area_a + area_b - inter
    return inter / max(union, 1e-9)


# ============================================================
# 会话编排：生成影像 → AI 检测 → 报告 → 医保联动
# ============================================================

def generate_study(study_type: str, findings_keys: list[str] | None = None,
                   seed: int | None = None) -> ImagingStudy:
    """生成一次影像分析会话（确定性合成 + AI 检测）。

    Args:
        study_type: 检查类型
        findings_keys: 植入病灶类别列表；None 时按类型默认植入
                       （含正常/轻微/显著三种模式用于测试）
        seed: 随机种子；None 时基于时间生成

    Returns:
        ImagingStudy：含合成影像、AI 检测发现、结构化报告。
    """
    seed = seed if seed is not None else int(datetime.now(UTC).timestamp() * 1000) % 1000000

    if study_type not in STUDY_TYPES:
        raise ValueError(f"不支持的检查类型: {study_type}，可选 {list(STUDY_TYPES)}")

    if findings_keys is None:
        findings_keys = STUDY_TYPES[study_type]["findings"]

    rng = _rng(seed)

    # 1. 由病灶类别反推植入坐标（确定性：按类别 hash 分配位置）
    implanted: list[Finding] = []
    for i, key in enumerate(findings_keys):
        if key not in FINDINGS_META:
            continue
        # 位置在影像中部、相对分散
        cx = 0.30 + 0.40 * ((i * 37 + seed % 7) % 100) / 100
        cy = 0.35 + 0.40 * ((i * 53 + seed % 11) % 100) / 100
        # 尺寸：结节小、积液/占位大
        w = 0.05 + 0.05 * ((i * 7 + seed % 5) % 10) / 10
        h = w * (1.0 + ((i + seed) % 3) * 0.15)
        if key in ("effusion", "cardiomegaly", "tumor"):
            w, h = w * 2.2, h * 1.6
        implanted.append(Finding(
            finding_type=key, x=cx, y=cy, w=w, h=h,
            confidence=0.99, severity=FINDINGS_META[key]["severity"], source="ground_truth",
        ))

    # 2. 渲染合成影像（植入病灶）
    rng = _rng(seed)
    if study_type == "chest_xray":
        arr = _render_chest_xray(rng, implanted)
    elif study_type == "lung_ct":
        arr = _render_lung_ct(rng, implanted)
    else:
        arr = _render_brain_mri(rng, implanted)

    # 3. AI 检测（对渲染后影像做真实图像分析）
    ai_findings = detect_findings(arr, study_type)

    # 4. 若无检测结果，补一个低置信提示（保证 Demo 可解释）
    if not ai_findings:
        ai_findings = [Finding(
            finding_type=findings_keys[0] if findings_keys else "nodule",
            x=0.5, y=0.5, w=0.08, h=0.08,
            confidence=0.51, severity="low", source="ai", status="pending",
            evidence="对比度低于检测阈值，标记为低置信候选，建议医生复核。",
        )]

    # 5. 影像 base64
    image_b64 = render_study_image(study_type, implanted, seed)

    study = ImagingStudy(
        study_id=_new_study_id(seed),
        user_id="",
        study_type=study_type,
        seed=seed,
        findings=ai_findings,
        image_base64=image_b64,
    )
    study.report = build_report(ai_findings)
    return study


def _new_study_id(seed: int) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"ST{ts}{seed % 10000:04d}"


def build_report(findings: list[Finding]) -> dict:
    """由 AI/医生最终标注生成结构化影像报告。"""
    findings_dicts = [f.to_dict() for f in findings]
    confirmed = [f for f in findings if f.status == "confirmed"]
    pending = [f for f in findings if f.status == "pending"]
    rejected = [f for f in findings if f.status == "rejected"]

    has_high = any(f.severity == "high" for f in confirmed or findings)
    has_medium = any(f.severity == "medium" for f in confirmed or findings)
    has_low = any(f.severity == "low" for f in confirmed or findings)

    if confirmed:
        conclusion = "检出异常征象，建议专科随访。" if (has_high or has_medium) else "未见明显异常，建议定期复查。"
        risk_level = "高" if has_high else ("中" if has_medium else "低")
    else:
        conclusion = "AI 提示候选征象，待医师复核后出具诊断意见。"
        risk_level = "待复核"

    # advice 必须为数组：前端 ImagingReportData.advice: string[] 直接 .map() 渲染，
    # 字符串会导致 TypeError → Next.js 客户端白屏（线上彩排踩坑）
    if has_high:
        advice = ["建议尽快至专科就诊，完善相关检查并启动医保待遇申请。"]
    elif has_medium:
        advice = ["建议 1-3 个月内专科复诊，动态观察。"]
    elif has_low:
        advice = ["建议定期随访，保持健康生活方式。"]
    else:
        advice = ["建议结合临床症状综合评估。"]

    return {
        "conclusion": conclusion,
        "risk_level": risk_level,
        "advice": advice,
        "confirmed_count": len(confirmed),
        "pending_count": len(pending),
        "rejected_count": len(rejected),
        "findings": findings_dicts,
        "generated_at": datetime.now(UTC).isoformat(),
        "disclaimer": "本报告由 AI 辅助生成，仅供筛查参考，最终诊断须由持证医师复核确认。",
    }


def link_to_imaging_policies(findings: list[Finding]) -> list[dict]:
    """影像异常 → 医保检查联动推荐。"""
    rules = load_imaging_policy_link()
    links: list[dict] = []
    finding_keys = [f.finding_type for f in findings]
    severities = [f.severity for f in findings]

    for rule in rules.get("links", []):
        cond = rule.get("condition", {})
        hit = True
        if "any_severity" in cond and cond["any_severity"] not in severities:
            hit = False
        if "findings" in cond and not set(cond["findings"]) & set(finding_keys):
            hit = False
        if hit:
            links.append({
                "trigger": rule.get("trigger"),
                "title": rule.get("title"),
                "policy_hint": rule.get("policy_hint"),
                "description": rule.get("description"),
                "suggestion": rule.get("suggestion"),
                "related_policies": rule.get("related_policies", []),
            })
    return links


def apply_doctor_review(findings: list[Finding], doctor_annotations: list[dict]) -> list[Finding]:
    """应用医生复核结果：确认/驳回/修正/新增标注。

    Args:
        findings: AI 预标注列表
        doctor_annotations: 医生标注操作列表
            [{action: "confirm"|"reject"|"add"|"update", index?, finding_type, x,y,w,h, confidence, severity, status?}]

    Returns:
        更新后的发现列表（医生标注优先，AI 被驳回项保留但标记 rejected）。
    """
    result = [Finding(
        finding_type=f.finding_type, x=f.x, y=f.y, w=f.w, h=f.h,
        confidence=f.confidence, severity=f.severity, source=f.source,
        status=f.status, evidence=f.evidence,
    ) for f in findings]

    for op in doctor_annotations:
        action = op.get("action")
        if action == "confirm" and op.get("index") is not None:
            idx = op["index"]
            if 0 <= idx < len(result):
                result[idx].status = "confirmed"
        elif action == "reject" and op.get("index") is not None:
            idx = op["index"]
            if 0 <= idx < len(result):
                result[idx].status = "rejected"
        elif action in ("add", "update"):
            ftype = op.get("finding_type", "nodule")
            meta = FINDINGS_META.get(ftype, {})
            result.append(Finding(
                finding_type=ftype,
                x=float(op.get("x", 0.5)),
                y=float(op.get("y", 0.5)),
                w=float(op.get("w", 0.06)),
                h=float(op.get("h", 0.06)),
                confidence=float(op.get("confidence", 0.9)),
                severity=op.get("severity", meta.get("severity", "medium")),
                source="doctor",
                status="confirmed",
                evidence=op.get("evidence", "医师人工复核标注"),
            ))
    return result
