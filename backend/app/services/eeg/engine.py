"""
瓯医数链 - 脑电健康引擎（EEG Engine）

BCI×医保创新核心模块 —— 将 EEG 脑电指标纳入医保健康画像
- 合成 EEG 信号生成（4 通道，256Hz，模拟不同心理状态）
- 频域特征提取（δ/θ/α/β/γ 五频段功率谱密度）
- 脑电健康指标计算（压力/注意力/睡眠/认知负荷/情绪）
- 脑电异常 → 医保政策联动推荐
- 每条评估带 evidence（数据证据），支撑"可解释性"

技术栈：numpy（FFT 频域分析）+ 规则引擎
设计依据：DEAP/SEED 情绪识别模型 + 临床脑电频段共识
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================
# 常量：频段定义 / 采样率 / 通道
# ============================================================

SAMPLE_RATE = 256  # Hz，消费级 EEG 设备标准采样率
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]  # Muse 4 通道布局
WINDOW_SECONDS = 4  # 单次分析窗口 4 秒（1024 采样点）

# 五大频段（单位 Hz）
BANDS = {
    "delta": (0.5, 4),    # δ 波：深度睡眠
    "theta": (4, 8),      # θ 波：浅睡/疲劳/记忆
    "alpha": (8, 13),     # α 波：放松/清醒
    "beta": (13, 30),     # β 波：专注/焦虑
    "gamma": (30, 45),    # γ 波：高度认知（消费级设备上限 45Hz）
}

# 心理状态预设（用于合成信号生成，模拟真实场景）
MENTAL_STATES = {
    "relaxed": {"delta": 0.3, "theta": 0.5, "alpha": 1.0, "beta": 0.3, "gamma": 0.1,
                "label": "放松", "stress": 20, "attention": 50, "sleep": 85, "cognitive": 30},
    "focused": {"delta": 0.2, "theta": 0.4, "alpha": 0.5, "beta": 0.9, "gamma": 0.4,
                "label": "专注", "stress": 40, "attention": 88, "sleep": 65, "cognitive": 75},
    "stressed": {"delta": 0.2, "theta": 0.4, "alpha": 0.3, "beta": 1.0, "gamma": 0.5,
                 "label": "高压力", "stress": 85, "attention": 60, "sleep": 45, "cognitive": 80},
    "fatigued": {"delta": 0.6, "theta": 0.9, "alpha": 0.7, "beta": 0.2, "gamma": 0.1,
                 "label": "疲劳", "stress": 50, "attention": 30, "sleep": 40, "cognitive": 35},
    "sleep_deprived": {"delta": 0.8, "theta": 0.7, "alpha": 0.4, "beta": 0.3, "gamma": 0.1,
                       "label": "睡眠不足", "stress": 60, "attention": 35, "sleep": 25, "cognitive": 40},
}

# 脑电异常 → 医保政策联动映射
# engine.py 位于 backend/app/services/eeg/，数据文件在 oumed-chain/data/
# 向上 5 层：eeg/ → services/ → app/ → backend/ → oumed-chain/
_EEG_POLICY_LINK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    "data", "eeg_policy_link.json",
)
_EEG_POLICY_LINK_CACHE: dict | None = None


def load_eeg_policy_link() -> dict:
    """加载脑电-医保政策联动规则库（带缓存）。"""
    global _EEG_POLICY_LINK_CACHE
    if _EEG_POLICY_LINK_CACHE is not None:
        return _EEG_POLICY_LINK_CACHE
    try:
        with open(_EEG_POLICY_LINK_PATH, encoding="utf-8") as f:
            _EEG_POLICY_LINK_CACHE = json.load(f)
        logger.info("加载脑电-医保政策联动规则库: %s", _EEG_POLICY_LINK_PATH)
    except Exception as e:
        logger.warning("加载脑电政策联动规则库失败: %s，使用内置默认规则", e)
        _EEG_POLICY_LINK_CACHE = _default_policy_link()
    return _EEG_POLICY_LINK_CACHE


def _default_policy_link() -> dict:
    """内置默认联动规则（文件缺失时兜底）。"""
    return {
        "links": [
            {
                "trigger": "high_stress",
                "condition": {"stress_index_min": 70},
                "title": "持续高压力状态",
                "policy_hint": "心理科门诊报销政策",
                "description": "检测到持续高压力脑电模式，长期压力可能影响心血管和心理健康。",
                "suggestion": "建议关注心理健康，可咨询心理科门诊。多地已将心理治疗纳入医保统筹。",
                "related_policies": ["心理治疗医保报销", "门诊慢病待遇（抑郁/焦虑）"],
            },
            {
                "trigger": "poor_sleep",
                "condition": {"sleep_quality_max": 40},
                "title": "睡眠质量持续下降",
                "policy_hint": "睡眠障碍相关检查报销政策",
                "description": "脑电 δ/θ 波比例异常，提示睡眠质量不佳，可能存在睡眠障碍。",
                "suggestion": "建议进行睡眠监测（多导睡眠图），部分城市已纳入医保报销。",
                "related_policies": ["睡眠监测医保报销", "门诊特殊病种（睡眠障碍）"],
            },
            {
                "trigger": "cognitive_overload",
                "condition": {"cognitive_load_min": 75},
                "title": "认知负荷过高",
                "policy_hint": "认知功能评估相关医保政策",
                "description": "脑电 β/γ 波持续偏高，提示认知负荷过重，需关注脑健康。",
                "suggestion": "建议进行认知功能筛查，65 岁以上可享受老年认知评估医保待遇。",
                "related_policies": ["老年认知功能筛查", "门诊慢病待遇（阿尔茨海默病）"],
            },
            {
                "trigger": "attention_low",
                "condition": {"attention_index_max": 40},
                "title": "注意力持续偏低",
                "policy_hint": "注意力评估相关医保政策",
                "description": "θ/β 比值偏高，提示注意力水平下降，可能影响日常生活。",
                "suggestion": "建议关注注意力变化，必要时进行神经内科评估。",
                "related_policies": ["神经内科门诊报销", "儿童注意力评估（学生医保）"],
            },
            # ⭐ 赛道7核心：脑血管疾病预警政策联动
            {
                "trigger": "cerebrovascular_risk",
                "condition": {"cerebrovascular_risk_min": 60},
                "title": "脑血管疾病风险预警",
                "policy_hint": "脑血管病检查与治疗医保政策",
                "description": "脑电频段异常提示脑血管功能风险，建议尽快进行脑血管专项检查。",
                "suggestion": "建议进行颈动脉超声/经颅多普勒/头颅CT检查。脑血管病已纳入门诊慢病医保待遇，急性脑卒中可走绿色通道。",
                "related_policies": ["脑血管病门诊慢病待遇", "脑卒中绿色通道", "颈动脉超声医保报销", "经颅多普勒医保报销"],
            },
            # ⭐ 赛道7核心：认知衰退筛查政策联动
            {
                "trigger": "cognitive_decline_risk",
                "condition": {"cognitive_decline_risk_min": 60},
                "title": "认知功能衰退风险",
                "policy_hint": "认知功能评估与治疗医保政策",
                "description": "脑电特征符合轻度认知障碍(MCI)，建议进行认知功能评估。",
                "suggestion": "建议进行MMSE/MoCA认知功能量表评估。65岁以上可享受老年认知评估医保待遇，阿尔茨海默病已纳入门诊慢病保障。",
                "related_policies": ["老年认知功能筛查", "门诊慢病待遇（阿尔茨海默病）", "神经内科门诊报销"],
            },
            # ⭐ 赛道7核心：精神状态筛查政策联动
            {
                "trigger": "mental_health_risk",
                "condition": {"mental_health_overall_min": 60},
                "title": "精神状态筛查异常",
                "policy_hint": "精神卫生医保政策",
                "description": "脑电精神状态筛查显示焦虑/抑郁倾向，建议进行专业评估。",
                "suggestion": "建议咨询精神卫生科或心理科。多地已将心理治疗纳入医保统筹，抑郁症/焦虑症可申请门诊慢病待遇。",
                "related_policies": ["心理治疗医保报销", "门诊慢病待遇（抑郁症/焦虑症）", "精神卫生专科报销"],
            },
        ]
    }


# ============================================================
# 数据结构
# ============================================================

@dataclass
class EEGSession:
    """一次 EEG 采集会话的完整评估结果"""
    session_id: str
    user_id: str
    timestamp: str
    duration_seconds: int
    channels: list[str]
    sample_rate: int
    mental_state: str                  # relaxed/focused/stressed/fatigued/sleep_deprived
    mental_state_label: str
    band_powers: dict[str, dict]       # {channel: {delta/theta/alpha/beta/gamma: value}}
    avg_band_powers: dict[str, float]  # 五频段平均功率
    metrics: dict                      # stress/attention/sleep/cognitive/emotion
    alerts: list[dict]                 # 脑电异常预警（带 evidence）
    policy_links: list[dict]           # 医保政策联动推荐
    summary: str                       # 总体评估
    waveform: list[dict]               # 降采样波形数据（前端可视化用）
    # v2.2 新增：真实设备/文件导入的溯源信息
    source: str = "synthetic"          # synthetic / device / file
    device_info: dict = field(default_factory=dict)  # 设备信息（来源、质量等）

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "duration_seconds": self.duration_seconds,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "mental_state": self.mental_state,
            "mental_state_label": self.mental_state_label,
            "band_powers": self.band_powers,
            "avg_band_powers": self.avg_band_powers,
            "metrics": self.metrics,
            "alerts": self.alerts,
            "policy_links": self.policy_links,
            "summary": self.summary,
            "waveform": self.waveform,
            "source": self.source,
            "device_info": self.device_info,
        }


# ============================================================
# 1. 合成 EEG 信号生成（模拟消费级设备采集）
# ============================================================

def generate_synthetic_eeg(
    mental_state: str = "relaxed",
    duration_seconds: int = WINDOW_SECONDS,
    sample_rate: int = SAMPLE_RATE,
    channels: list[str] = None,
    noise_level: float = 0.15,
    seed: int | None = None,
) -> tuple[list[np.ndarray], list[str], int]:
    """生成合成 EEG 信号（模拟 Muse 4 通道采集）。

    基于心理状态预设的频段权重，叠加多频段正弦波 + 工频噪声 + 白噪声，
    生成接近真实 EEG 频谱特性的信号。

    Args:
        mental_state: 心理状态（relaxed/focused/stressed/fatigued/sleep_deprived）
        duration_seconds: 采集时长（秒）
        sample_rate: 采样率（Hz）
        channels: 通道名列表
        noise_level: 噪声水平（0-1）
        seed: 随机种子（可复现）

    Returns:
        signals: list[np.ndarray]，每个通道的信号
        channels: 通道名列表
        sample_rate: 采样率
    """
    if channels is None:
        channels = CHANNELS
    if mental_state not in MENTAL_STATES:
        mental_state = "relaxed"

    state = MENTAL_STATES[mental_state]
    rng = np.random.default_rng(seed)
    n_samples = duration_seconds * sample_rate
    t = np.arange(n_samples) / sample_rate

    signals = []
    for i, _ch in enumerate(channels):
        # 各频段正弦波叠加（振幅由状态权重决定）
        signal = np.zeros(n_samples, dtype=np.float64)
        # δ 波（0.5-4Hz）
        for freq in [1.0, 2.0, 3.0]:
            signal += state["delta"] * 8.0 * np.sin(2 * np.pi * freq * t + rng.random())
        # θ 波（4-8Hz）
        for freq in [5.0, 6.0, 7.0]:
            signal += state["theta"] * 6.0 * np.sin(2 * np.pi * freq * t + rng.random())
        # α 波（8-13Hz）
        for freq in [9.0, 10.0, 11.0]:
            signal += state["alpha"] * 10.0 * np.sin(2 * np.pi * freq * t + rng.random())
        # β 波（13-30Hz）
        for freq in [15.0, 20.0, 25.0]:
            signal += state["beta"] * 4.0 * np.sin(2 * np.pi * freq * t + rng.random())
        # γ 波（30-45Hz）
        for freq in [35.0, 40.0]:
            signal += state["gamma"] * 2.0 * np.sin(2 * np.pi * freq * t + rng.random())

        # 通道间差异（模拟电极位置差异）
        signal *= 1.0 + 0.1 * (i - 1.5)

        # 50Hz 工频干扰
        signal += 1.5 * np.sin(2 * np.pi * 50 * t)

        # 白噪声
        signal += noise_level * 20 * rng.standard_normal(n_samples)

        # 缓慢漂移（模拟电极接触变化）
        signal += 5 * np.sin(2 * np.pi * 0.1 * t + rng.random())

        signals.append(signal)

    return signals, channels, sample_rate


# ============================================================
# 2. 频域特征提取（FFT + 五频段功率谱密度）
# ============================================================

def extract_band_powers(
    signals: list[np.ndarray],
    sample_rate: int = SAMPLE_RATE,
) -> tuple[dict[str, dict], dict[str, float]]:
    """提取各通道五频段功率谱密度（PSD）。

    使用 Welch 平均周期图法计算 PSD，再按频段积分得到各频段功率。

    Args:
        signals: 多通道信号
        sample_rate: 采样率

    Returns:
        band_powers: {channel: {delta/theta/alpha/beta/gamma: power}}
        avg_powers: 五频段跨通道平均功率
    """
    band_powers = {}
    band_accum = {b: [] for b in BANDS}

    for i, signal in enumerate(signals):
        ch_name = CHANNELS[i] if i < len(CHANNELS) else f"ch{i+1}"
        powers = _compute_channel_powers(signal, sample_rate)
        band_powers[ch_name] = powers
        for b in BANDS:
            band_accum[b].append(powers[b])

    avg_powers = {b: float(np.mean(vals)) if vals else 0.0 for b, vals in band_accum.items()}
    return band_powers, avg_powers


def _compute_channel_powers(signal: np.ndarray, sample_rate: int) -> dict[str, float]:
    """单通道五频段功率计算（Welch PSD + 频段积分）。"""
    # 去均值
    signal = signal - np.mean(signal)
    n = len(signal)
    if n < 256:
        return dict.fromkeys(BANDS, 0.0)

    # Welch 法：分段 FFT 平均，降低方差
    nperseg = min(256, n)
    freqs, psd = _welch(signal, sample_rate, nperseg=nperseg)

    powers = {}
    for band, (lo, hi) in BANDS.items():
        mask = (freqs >= lo) & (freqs < hi)
        # 频段功率 = PSD 在该频段的积分（梯形法）
        if np.any(mask):
            # numpy 2.x 移除 np.trapz，改名为 np.trapezoid
            trap = getattr(np, "trapezoid", None) or np.trapz
            powers[band] = float(trap(psd[mask], freqs[mask]))
        else:
            powers[band] = 0.0
    return powers


def _welch(signal: np.ndarray, fs: int, nperseg: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """简化版 Welch PSD（Hann 窗 + 50% 重叠 + FFT）。"""
    n = len(signal)
    step = nperseg // 2
    window = np.hanning(nperseg)
    psd_sum = np.zeros(nperseg // 2 + 1)
    count = 0

    for start in range(0, n - nperseg + 1, step):
        seg = signal[start:start + nperseg] * window
        # FFT 功率谱
        fft = np.fft.rfft(seg)
        psd = (np.abs(fft) ** 2) / (fs * np.sum(window ** 2))
        psd_sum += psd
        count += 1

    if count == 0:
        count = 1
    psd_avg = psd_sum / count
    freqs = np.fft.rfftfreq(nperseg, d=1.0 / fs)
    return freqs, psd_avg


# ============================================================
# 3. 脑电健康指标计算
# ============================================================

def compute_health_metrics(avg_powers: dict[str, float]) -> dict:
    """基于五频段功率计算脑电健康指标。

    指标设计依据：
    - 压力指数：α/β 比值反演（α 高 β 低 = 放松 = 低压力）
    - 注意力指数：θ/β 比值反演（θ 低 β 高 = 专注 = 高注意力）
    - 睡眠质量：δ + θ 功率占比（深睡相关频段充足 = 睡眠好）
    - 认知负荷：β + γ 功率占比（高频活动 = 高认知负荷）
    - 情绪状态：α 不对称性 + β 活跃度综合评估
    - 脑血管风险指数：δ 波异常激增 + θ 波抑制不足 + α 波减弱（临床脑电预警特征）
    - 认知衰退风险：θ/α 比值升高 + α 波抑制（认知功能下降脑电特征）
    - 精神状态筛查：基于情绪valence/arousal量化焦虑/抑郁倾向

    所有指标归一化到 0-100。
    """
    delta = max(avg_powers.get("delta", 0), 1e-6)
    theta = max(avg_powers.get("theta", 0), 1e-6)
    alpha = max(avg_powers.get("alpha", 0), 1e-6)
    beta = max(avg_powers.get("beta", 0), 1e-6)
    gamma = max(avg_powers.get("gamma", 0), 1e-6)
    total = delta + theta + alpha + beta + gamma

    # 1. 压力指数（0-100）：α/β 越低，压力越高
    alpha_beta_ratio = alpha / beta
    # ratio > 2 → 放松（低压力）；ratio < 0.5 → 高压力
    stress_index = _clamp(100 - (alpha_beta_ratio - 0.5) * 40, 0, 100)

    # 2. 注意力指数（0-100）：θ/β 越低，注意力越高
    theta_beta_ratio = theta / beta
    # ratio < 1 → 高注意力；ratio > 3 → 低注意力
    attention_index = _clamp(100 - (theta_beta_ratio - 0.5) * 35, 0, 100)

    # 3. 睡眠质量指数（0-100）：δ + θ 占比充足 = 睡眠好
    slow_wave_ratio = (delta + theta) / total
    # 0.3-0.6 为健康范围
    sleep_quality = _clamp((slow_wave_ratio - 0.1) * 200, 0, 100)

    # 4. 认知负荷（0-100）：β + γ 占比
    fast_wave_ratio = (beta + gamma) / total
    cognitive_load = _clamp(fast_wave_ratio * 250, 0, 100)

    # 5. 情绪状态（valence/arousal 简化版）
    # valence: α 活跃 → 积极；β 过高 → 消极
    emotion_valence = _clamp(50 + (alpha - beta) / total * 300, 0, 100)
    # arousal: β + γ 活跃 → 高唤醒
    emotion_arousal = _clamp(fast_wave_ratio * 200, 0, 100)
    if emotion_valence < 40 and emotion_arousal > 60:
        emotion_label = "焦虑倾向"
    elif emotion_valence < 40 and emotion_arousal < 40:
        emotion_label = "低落倾向"
    elif emotion_valence > 60 and emotion_arousal > 60:
        emotion_label = "积极兴奋"
    elif emotion_valence > 60 and emotion_arousal < 40:
        emotion_label = "平静放松"
    else:
        emotion_label = "情绪平稳"

    # 6. 脑血管风险指数（0-100，越高风险越大）⭐ 赛道7核心要求
    # 临床依据：脑血管病变早期脑电特征——δ波弥漫性增多 + θ波抑制不足 + α波节律减弱
    # 参考：脑血管病脑电图诊断共识（中华神经科学会）
    delta_ratio = delta / total  # δ波占比，正常<0.25，>0.35提示异常
    theta_alpha_ratio = theta / alpha  # θ/α比值，正常<0.8，>1.2提示皮层功能抑制
    alpha_suppression = _clamp(100 - (alpha / total) * 400, 0, 100)  # α波抑制程度
    # 综合风险：δ波激增(40%) + θ/α升高(35%) + α波抑制(25%)
    cerebrovascular_risk = _clamp(
        (delta_ratio - 0.2) * 160 +  # δ波占比超过0.2开始计分
        (theta_alpha_ratio - 0.5) * 50 +  # θ/α超过0.5开始计分
        alpha_suppression * 0.25,
        0, 100
    )

    # 7. 认知衰退风险（0-100，越高风险越大）⭐ 赛道7核心要求
    # 临床依据：轻度认知障碍(MCI)脑电特征——θ波相对功率增加 + α波相对功率降低 + θ/α比值升高
    # 参考：MCI脑电标志物研究（J Alzheimer's Dis, 2019）
    theta_alpha_ratio_cog = theta / alpha
    alpha_relative = alpha / total  # α波相对功率，正常>0.3
    theta_relative = theta / total  # θ波相对功率，正常<0.2
    cognitive_decline_risk = _clamp(
        (theta_alpha_ratio_cog - 0.5) * 60 +  # θ/α比值升高
        (0.3 - alpha_relative) * 200 +  # α波相对功率降低
        (theta_relative - 0.15) * 150,  # θ波相对功率升高
        0, 100
    )

    # 8. 精神状态筛查（焦虑/抑郁倾向量化，0-100，越高倾向越明显）⭐ 赛道7核心要求
    # 临床依据：焦虑抑郁脑电特征——左额区α波不对称性降低 + β波过度活跃 + γ波异常
    # 参考：抑郁症脑电定量分析共识（中国心理卫生杂志）
    # 简化模型：基于valence/arousal + β波过度活跃综合评估
    anxiety_score = _clamp(
        (100 - emotion_valence) * 0.4 +  # 效价低→焦虑倾向
        emotion_arousal * 0.3 +  # 高唤醒→焦虑
        (beta / total - 0.2) * 200 * 0.3,  # β波过度活跃
        0, 100
    )
    depression_score = _clamp(
        (100 - emotion_valence) * 0.5 +  # 效价低→抑郁倾向
        (100 - emotion_arousal) * 0.3 +  # 低唤醒→抑郁
        (theta / total - 0.15) * 150 * 0.2,  # θ波增多（抑郁相关）
        0, 100
    )
    # 综合精神状态风险（取较高者）
    mental_health_risk = max(anxiety_score, depression_score)
    if anxiety_score > depression_score and anxiety_score >= 60:
        mental_state_screening = "焦虑倾向"
    elif depression_score >= 60:
        mental_state_screening = "抑郁倾向"
    elif mental_health_risk >= 40:
        mental_state_screening = "情绪风险"
    else:
        mental_state_screening = "正常"

    return {
        "stress_index": round(stress_index, 1),
        "attention_index": round(attention_index, 1),
        "sleep_quality": round(sleep_quality, 1),
        "cognitive_load": round(cognitive_load, 1),
        "emotion": {
            "valence": round(emotion_valence, 1),
            "arousal": round(emotion_arousal, 1),
            "label": emotion_label,
        },
        "ratios": {
            "alpha_beta": round(alpha_beta_ratio, 3),
            "theta_beta": round(theta_beta_ratio, 3),
            "slow_wave_ratio": round(slow_wave_ratio, 3),
            "fast_wave_ratio": round(fast_wave_ratio, 3),
            "theta_alpha": round(theta_alpha_ratio, 3),
            "delta_ratio": round(delta_ratio, 3),
        },
        # ⭐ 赛道7核心：脑血管疾病预警
        "cerebrovascular_risk": round(cerebrovascular_risk, 1),
        # ⭐ 赛道7核心：认知衰退早期筛查
        "cognitive_decline_risk": round(cognitive_decline_risk, 1),
        # ⭐ 赛道7核心：精神状态早期筛查
        "mental_health": {
            "anxiety_score": round(anxiety_score, 1),
            "depression_score": round(depression_score, 1),
            "overall_risk": round(mental_health_risk, 1),
            "screening_label": mental_state_screening,
        },
    }


# ============================================================
# 4. 脑电异常预警 + 医保政策联动
# ============================================================

def scan_eeg_alerts(metrics: dict, user_profile: dict | None = None) -> list[dict]:
    """扫描脑电健康指标，生成异常预警（带 evidence）。"""
    alerts = []
    now_iso = datetime.now(UTC).isoformat()
    name = (user_profile or {}).get("name", "您")
    age = (user_profile or {}).get("age", 50)

    stress = metrics.get("stress_index", 0)
    attention = metrics.get("attention_index", 0)
    sleep = metrics.get("sleep_quality", 0)
    cognitive = metrics.get("cognitive_load", 0)
    emotion = metrics.get("emotion", {})
    emotion_label = emotion.get("label", "情绪平稳")
    # ⭐ 新增指标
    cerebrovascular = metrics.get("cerebrovascular_risk", 0)
    cognitive_decline = metrics.get("cognitive_decline_risk", 0)
    mental_health = metrics.get("mental_health", {})
    mental_screening = mental_health.get("screening_label", "正常")
    anxiety = mental_health.get("anxiety_score", 0)
    depression = mental_health.get("depression_score", 0)

    # 规则 1：高压力
    if stress >= 70:
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": "持续高压力状态",
            "description": f"检测到{name}压力指数 {stress}/100，α/β 比值偏低，提示长期处于紧张状态。长期高压可能影响心血管和心理健康。",
            "suggestion": "建议进行心理放松训练，必要时咨询心理科门诊。多地已将心理治疗纳入医保统筹。",
            "action": "建议关注心理健康，可咨询心理科门诊",
            "category": "stress",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "stress_index", "value": stress, "threshold": 70},
                {"type": "eeg_ratio", "ratio": "alpha_beta", "value": metrics.get("ratios", {}).get("alpha_beta")},
            ],
        })
    elif stress >= 50:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "压力水平偏高",
            "description": f"压力指数 {stress}/100，建议适当放松。",
            "suggestion": "建议进行深呼吸、冥想等放松训练",
            "action": "建议适当放松",
            "category": "stress",
            "timestamp": now_iso,
            "evidence": [{"type": "eeg_metric", "metric": "stress_index", "value": stress}],
        })

    # 规则 2：睡眠质量差
    if sleep < 40:
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": "睡眠质量持续下降",
            "description": f"睡眠质量指数 {sleep}/100，δ/θ 波比例异常，提示睡眠质量不佳，可能存在睡眠障碍。",
            "suggestion": "建议进行睡眠监测（多导睡眠图），部分城市已纳入医保报销。",
            "action": "建议进行睡眠监测评估",
            "category": "sleep",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "sleep_quality", "value": sleep, "threshold": 40},
                {"type": "eeg_ratio", "ratio": "slow_wave_ratio", "value": metrics.get("ratios", {}).get("slow_wave_ratio")},
            ],
        })
    elif sleep < 60:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "睡眠质量一般",
            "description": f"睡眠质量指数 {sleep}/100，建议改善睡眠习惯。",
            "suggestion": "建议保持规律作息，睡前避免使用电子设备",
            "action": "建议改善睡眠习惯",
            "category": "sleep",
            "timestamp": now_iso,
            "evidence": [{"type": "eeg_metric", "metric": "sleep_quality", "value": sleep}],
        })

    # 规则 3：认知负荷过高
    if cognitive >= 75:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "认知负荷过高",
            "description": f"认知负荷指数 {cognitive}/100，β/γ 波持续偏高，提示脑力消耗过大。",
            "suggestion": "建议适当休息，65 岁以上可关注老年认知评估医保待遇。",
            "action": "建议适当休息，关注脑健康",
            "category": "cognitive",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "cognitive_load", "value": cognitive, "threshold": 75},
                {"type": "eeg_ratio", "ratio": "fast_wave_ratio", "value": metrics.get("ratios", {}).get("fast_wave_ratio")},
            ],
        })

    # 规则 4：注意力偏低
    if attention < 40:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "注意力持续偏低",
            "description": f"注意力指数 {attention}/100，θ/β 比值偏高，提示注意力水平下降。",
            "suggestion": "建议关注注意力变化，必要时进行神经内科评估。",
            "action": "建议关注注意力变化",
            "category": "attention",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "attention_index", "value": attention, "threshold": 40},
                {"type": "eeg_ratio", "ratio": "theta_beta", "value": metrics.get("ratios", {}).get("theta_beta")},
            ],
        })

    # 规则 5：情绪异常
    if emotion_label in ("焦虑倾向", "低落倾向"):
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": f"情绪状态：{emotion_label}",
            "description": f"脑电情绪评估显示{emotion_label}（效价 {emotion.get('valence', 0)}/100，唤醒 {emotion.get('arousal', 0)}/100）。",
            "suggestion": "建议关注情绪健康，持续异常可咨询心理科。多地心理治疗已纳入医保。",
            "action": "建议关注情绪健康",
            "category": "emotion",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_emotion", "valence": emotion.get("valence"), "arousal": emotion.get("arousal"), "label": emotion_label},
            ],
        })

    # ⭐ 规则 6：脑血管疾病风险预警（赛道7核心要求）
    if cerebrovascular >= 60:
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": "脑血管疾病风险预警",
            "description": f"脑血管风险指数 {cerebrovascular}/100，δ波弥漫性增多 + θ/α比值升高 + α波节律减弱，提示脑血管功能异常风险。{name}（{age}岁）建议尽快进行脑血管专项检查。",
            "suggestion": "建议尽快进行颈动脉超声/经颅多普勒/头颅CT检查。脑血管病已纳入门诊慢病医保待遇，急性脑卒中可走绿色通道。",
            "action": "建议尽快进行脑血管专项检查",
            "category": "cerebrovascular",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "cerebrovascular_risk", "value": cerebrovascular, "threshold": 60},
                {"type": "eeg_ratio", "ratio": "delta_ratio", "value": metrics.get("ratios", {}).get("delta_ratio"), "normal_range": "<0.25"},
                {"type": "eeg_ratio", "ratio": "theta_alpha", "value": metrics.get("ratios", {}).get("theta_alpha"), "normal_range": "<0.8"},
            ],
        })
    elif cerebrovascular >= 40:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "脑血管风险偏高",
            "description": f"脑血管风险指数 {cerebrovascular}/100，脑电频段比例轻度异常，建议关注脑血管健康。",
            "suggestion": "建议控制血压血糖血脂，定期体检。高血压糖尿病等慢病已纳入门诊慢病医保待遇。",
            "action": "建议关注脑血管健康，控制慢病危险因素",
            "category": "cerebrovascular",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "cerebrovascular_risk", "value": cerebrovascular, "threshold": 40},
            ],
        })

    # ⭐ 规则 7：认知衰退风险预警（赛道7核心要求）
    if cognitive_decline >= 60:
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": "认知功能衰退风险预警",
            "description": f"认知衰退风险指数 {cognitive_decline}/100，θ波相对功率增加 + α波相对功率降低 + θ/α比值升高，符合轻度认知障碍(MCI)脑电特征。{name}（{age}岁）建议进行认知功能评估。",
            "suggestion": "建议进行MMSE/MoCA认知功能量表评估。65岁以上可享受老年认知评估医保待遇，阿尔茨海默病已纳入门诊慢病保障。",
            "action": "建议进行认知功能筛查评估",
            "category": "cognitive_decline",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "cognitive_decline_risk", "value": cognitive_decline, "threshold": 60},
                {"type": "eeg_ratio", "ratio": "theta_alpha", "value": metrics.get("ratios", {}).get("theta_alpha"), "normal_range": "<0.8"},
            ],
        })
    elif cognitive_decline >= 40:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "认知功能下降趋势",
            "description": f"认知衰退风险指数 {cognitive_decline}/100，脑电显示轻度认知功能下降趋势，建议关注。",
            "suggestion": "建议保持脑力活动，定期进行认知功能自测。可咨询神经内科。",
            "action": "建议关注认知功能变化",
            "category": "cognitive_decline",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "cognitive_decline_risk", "value": cognitive_decline, "threshold": 40},
            ],
        })

    # ⭐ 规则 8：精神状态异常预警（赛道7核心要求）
    if mental_screening in ("焦虑倾向", "抑郁倾向") and mental_health.get("overall_risk", 0) >= 60:
        alerts.append({
            "level": "high", "severity": "high", "icon": "🔴",
            "title": f"精神状态筛查：{mental_screening}",
            "description": f"精神状态筛查显示{mental_screening}（焦虑评分 {anxiety}/100，抑郁评分 {depression}/100）。脑电β波过度活跃/θ波增多，提示需要专业评估。",
            "suggestion": "建议尽快咨询精神卫生科或心理科。多地已将心理治疗纳入医保统筹，抑郁症/焦虑症可申请门诊慢病待遇。",
            "action": "建议咨询精神卫生科或心理科",
            "category": "mental_health",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "anxiety_score", "value": anxiety, "threshold": 60},
                {"type": "eeg_metric", "metric": "depression_score", "value": depression, "threshold": 60},
                {"type": "eeg_screening", "label": mental_screening},
            ],
        })
    elif mental_screening == "情绪风险" and mental_health.get("overall_risk", 0) >= 40:
        alerts.append({
            "level": "medium", "severity": "medium", "icon": "🟡",
            "title": "情绪状态需关注",
            "description": f"精神状态筛查显示情绪风险（焦虑评分 {anxiety}/100，抑郁评分 {depression}/100），建议关注心理健康。",
            "suggestion": "建议进行心理自评量表（如PHQ-9/GAD-7），必要时咨询心理科。",
            "action": "建议关注心理健康，进行自评筛查",
            "category": "mental_health",
            "timestamp": now_iso,
            "evidence": [
                {"type": "eeg_metric", "metric": "overall_risk", "value": mental_health.get("overall_risk"), "threshold": 40},
            ],
        })

    # 按等级排序
    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: order.get(a.get("level", "low"), 3))
    return alerts


def link_to_policies(metrics: dict, user_profile: dict | None = None) -> list[dict]:
    """脑电异常 → 医保政策联动推荐。

    核心创新：检测到脑电异常 → 自动推荐相关医保政策，
    体现"脑电采集 → 健康评估 → 医保联动"全链路。
    """
    rules = load_eeg_policy_link()
    links = rules.get("links", [])
    results = []
    name = (user_profile or {}).get("name", "您")

    for link in links:
        cond = link.get("condition", {})
        triggered = False
        if "stress_index_min" in cond and metrics.get("stress_index", 0) >= cond["stress_index_min"]:
            triggered = True
        if "sleep_quality_max" in cond and metrics.get("sleep_quality", 100) <= cond["sleep_quality_max"]:
            triggered = True
        if "cognitive_load_min" in cond and metrics.get("cognitive_load", 0) >= cond["cognitive_load_min"]:
            triggered = True
        if "attention_index_max" in cond and metrics.get("attention_index", 100) <= cond["attention_index_max"]:
            triggered = True
        # ⭐ 新增触发条件
        if "cerebrovascular_risk_min" in cond and metrics.get("cerebrovascular_risk", 0) >= cond["cerebrovascular_risk_min"]:
            triggered = True
        if "cognitive_decline_risk_min" in cond and metrics.get("cognitive_decline_risk", 0) >= cond["cognitive_decline_risk_min"]:
            triggered = True
        if "mental_health_overall_min" in cond:
            mh = metrics.get("mental_health", {})
            if mh.get("overall_risk", 0) >= cond["mental_health_overall_min"]:
                triggered = True
        if "emotion_valence_max" in cond and metrics.get("emotion", {}).get("valence", 100) <= cond["emotion_valence_max"]:
            triggered = True

        if triggered:
            results.append({
                "trigger": link.get("trigger"),
                "title": link.get("title"),
                "policy_hint": link.get("policy_hint"),
                "description": link.get("description"),
                "suggestion": link.get("suggestion"),
                "related_policies": link.get("related_policies", []),
                "evidence": [{"type": "eeg_trigger", "trigger": link.get("trigger"), "user": name}],
            })
    return results


# ============================================================
# 5. 主入口：完整 EEG 会话评估
# ============================================================

def assess_session(
    user_id: str,
    mental_state: str = "relaxed",
    duration_seconds: int = WINDOW_SECONDS,
    user_profile: dict | None = None,
    seed: int | None = None,
) -> EEGSession:
    """完整 EEG 会话评估主入口。

    流程：合成信号 → 频域特征提取 → 健康指标计算 → 异常预警 → 医保政策联动

    Args:
        user_id: 用户 ID
        mental_state: 心理状态（模拟采集场景）
        duration_seconds: 采集时长
        user_profile: 用户画像（用于个性化联动）
        seed: 随机种子

    Returns:
        EEGSession 完整评估结果
    """
    if mental_state not in MENTAL_STATES:
        mental_state = "relaxed"
    state_meta = MENTAL_STATES[mental_state]

    # 1. 生成合成 EEG 信号
    signals, channels, sr = generate_synthetic_eeg(
        mental_state=mental_state,
        duration_seconds=duration_seconds,
        sample_rate=SAMPLE_RATE,
        channels=CHANNELS,
        seed=seed,
    )

    # 2. 频域特征提取
    band_powers, avg_powers = extract_band_powers(signals, sr)

    # 3. 健康指标计算
    metrics = compute_health_metrics(avg_powers)

    # 4. 异常预警
    alerts = scan_eeg_alerts(metrics, user_profile)

    # 5. 医保政策联动
    policy_links = link_to_policies(metrics, user_profile)

    # 6. 降采样波形（前端可视化用，每通道 128 点）
    waveform = _downsample_waveform(signals, channels, target_points=128)

    # 7. 汇总
    summary = _build_summary(
        user_profile, mental_state, state_meta["label"],
        metrics, alerts, policy_links,
    )

    session_id = f"eeg_{user_id}_{int(datetime.now(UTC).timestamp())}"
    return EEGSession(
        session_id=session_id,
        user_id=str(user_id),
        timestamp=datetime.now(UTC).isoformat(),
        duration_seconds=duration_seconds,
        channels=channels,
        sample_rate=sr,
        mental_state=mental_state,
        mental_state_label=state_meta["label"],
        band_powers=band_powers,
        avg_band_powers=avg_powers,
        metrics=metrics,
        alerts=alerts,
        policy_links=policy_links,
        summary=summary,
        waveform=waveform,
    )


def assess_real_session(
    user_id: str,
    signals: list[np.ndarray],
    channels: list[str],
    sample_rate: int,
    mental_state: str = "auto",
    user_profile: dict | None = None,
    device_info: dict | None = None,
) -> EEGSession:
    """真实 EEG 信号评估主入口（v2.2 新增）。

    与 assess_session 的区别：跳过合成信号生成，直接使用传入的真实信号，
    后续的频域分析、指标计算、预警、政策联动逻辑完全复用。

    Args:
        user_id: 用户 ID
        signals: 多通道真实 EEG 信号（每个 ndarray 是一个通道）
        channels: 通道名列表
        sample_rate: 采样率（Hz）
        mental_state: 心理状态标签（用于结果标注，"auto" 则根据信号推断）
        user_profile: 用户画像
        device_info: 设备信息（来源、设备名、信号质量等）

    Returns:
        EEGSession 完整评估结果（source="device" 或 "file"）
    """
    # auto 模式：根据信号特征推断心理状态
    if mental_state == "auto" or mental_state not in MENTAL_STATES:
        # 先做频域分析，根据 α/β 比值推断
        _, avg_powers = extract_band_powers(signals, sample_rate)
        alpha = avg_powers.get("alpha", 0)
        beta = avg_powers.get("beta", 0)
        theta = avg_powers.get("theta", 0)
        delta = avg_powers.get("delta", 0)

        # 简单推断规则
        if alpha > beta * 1.5:
            mental_state = "relaxed"
        elif beta > alpha * 1.5:
            mental_state = "stressed"
        elif theta > alpha:
            mental_state = "fatigued"
        elif delta > alpha:
            mental_state = "sleep_deprived"
        else:
            mental_state = "focused"
        logger.info("自动推断心理状态: %s (α=%.2f β=%.2f θ=%.2f δ=%.2f)",
                    mental_state, alpha, beta, theta, delta)

    state_meta = MENTAL_STATES[mental_state]

    # 2. 频域特征提取（复用已有逻辑）
    band_powers, avg_powers = extract_band_powers(signals, sample_rate)

    # 3. 健康指标计算
    metrics = compute_health_metrics(avg_powers)

    # 4. 异常预警
    alerts = scan_eeg_alerts(metrics, user_profile)

    # 5. 医保政策联动
    policy_links = link_to_policies(metrics, user_profile)

    # 6. 降采样波形
    waveform = _downsample_waveform(signals, channels, target_points=128)

    # 7. 汇总
    summary = _build_summary(
        user_profile, mental_state, state_meta["label"],
        metrics, alerts, policy_links,
    )

    # 8. 设备信息嵌入
    source = "device"
    if device_info and device_info.get("source") in ("file", "csv", "edf"):
        source = "file"

    session_id = f"eeg_{user_id}_{int(datetime.now(UTC).timestamp())}"
    session = EEGSession(
        session_id=session_id,
        user_id=str(user_id),
        timestamp=datetime.now(UTC).isoformat(),
        duration_seconds=round(len(signals[0]) / sample_rate, 2) if signals else 0,
        channels=channels,
        sample_rate=sample_rate,
        mental_state=mental_state,
        mental_state_label=state_meta["label"],
        band_powers=band_powers,
        avg_band_powers=avg_powers,
        metrics=metrics,
        alerts=alerts,
        policy_links=policy_links,
        summary=summary,
        waveform=waveform,
    )

    # 附加设备信息到结果（通过 to_dict 时会包含）
    session.device_info = device_info or {}
    session.source = source
    return session


def realtime_stream(
    mental_state: str = "relaxed",
    chunk_seconds: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
    seed: int | None = None,
) -> dict:
    """生成一个实时数据块（前端 SSE/轮询模拟实时采集）。

    返回单通道（TP9）降采样后的波形 + 当前频段功率快照。
    """
    signals, channels, sr = generate_synthetic_eeg(
        mental_state=mental_state,
        duration_seconds=max(1, int(chunk_seconds)),
        sample_rate=sample_rate,
        channels=[CHANNELS[0]],
        seed=seed,
    )
    signal = signals[0]
    # 降采样到 64 点
    target = 64
    step = max(1, len(signal) // target)
    waveform = signal[::step][:target].tolist()

    # 当前频段功率快照
    _, avg_powers = extract_band_powers([signal], sr)
    metrics = compute_health_metrics(avg_powers)

    return {
        "channel": channels[0],
        "waveform": [{"i": i, "v": float(v)} for i, v in enumerate(waveform)],
        "band_powers": avg_powers,
        "metrics_snapshot": {
            "stress_index": metrics["stress_index"],
            "attention_index": metrics["attention_index"],
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ============================================================
# 辅助函数
# ============================================================

def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _downsample_waveform(
    signals: list[np.ndarray], channels: list[str], target_points: int = 128,
) -> list[dict]:
    """降采样多通道波形，返回前端可绘制的结构。"""
    result = []
    for i, signal in enumerate(signals):
        ch = channels[i] if i < len(channels) else f"ch{i+1}"
        step = max(1, len(signal) // target_points)
        samples = signal[::step][:target_points].tolist()
        result.append({
            "channel": ch,
            "data": [{"i": i, "v": float(v)} for i, v in enumerate(samples)],
        })
    return result


def _build_summary(
    user_profile: dict | None,
    mental_state: str,
    state_label: str,
    metrics: dict,
    alerts: list,
    policy_links: list,
) -> str:
    name = (user_profile or {}).get("name", "您")
    stress = metrics.get("stress_index", 0)
    attention = metrics.get("attention_index", 0)
    sleep = metrics.get("sleep_quality", 0)
    cognitive = metrics.get("cognitive_load", 0)
    emotion_label = metrics.get("emotion", {}).get("label", "平稳")
    cerebrovascular = metrics.get("cerebrovascular_risk", 0)
    cognitive_decline = metrics.get("cognitive_decline_risk", 0)
    mental_screening = metrics.get("mental_health", {}).get("screening_label", "正常")

    parts = [f"脑电采集完成，{name}当前心理状态：{state_label}。"]
    parts.append(
        f"压力指数 {stress}/100，注意力 {attention}/100，睡眠质量 {sleep}/100，"
        f"认知负荷 {cognitive}/100，情绪：{emotion_label}。"
    )
    # ⭐ 新增赛道7核心指标
    parts.append(
        f"脑血管风险指数 {cerebrovascular}/100，认知衰退风险 {cognitive_decline}/100，"
        f"精神状态筛查：{mental_screening}。"
    )
    if alerts:
        parts.append(f"检测到 {len(alerts)} 项脑电健康预警。")
    if policy_links:
        parts.append(f"已为您匹配 {len(policy_links)} 项相关医保政策。")
    return "".join(parts)


def pick_mental_state_by_profile(user_profile: dict | None) -> str:
    """根据用户画像推荐合适的脑电采集场景（Demo 用）。

    慢病/高龄用户 → 模拟疲劳/睡眠不足；年轻健康用户 → 放松/专注。
    """
    if not user_profile or not user_profile.get("found"):
        return "relaxed"
    chronic = user_profile.get("chronic_diseases", []) or []
    age = user_profile.get("age", 50)
    if age >= 65 or len(chronic) >= 2:
        return random.choice(["fatigued", "sleep_deprived", "stressed"])
    if chronic:
        return random.choice(["stressed", "fatigued"])
    return random.choice(["relaxed", "focused"])
