"""
瓯医数链 - EEG 设备适配层（Device Adapter）

将不同来源的 EEG 信号统一为 (signals, channels, sample_rate) 格式，
直接喂给已有的分析引擎（engine.py），无需修改引擎核心逻辑。

支持的信号来源：
1. LSL 实时流（Muse / Emotiv / OpenBCI 等通过 LSL 推流的设备）
2. CSV 文件导入（通用格式，第一行通道名，后续每行采样点）
3. EDF 文件导入（临床标准格式，需 pyedflib 库）
4. NumPy 数组（编程接口，用于测试和集成）

设计原则：
- pylsl / pyedflib 为可选依赖，未安装时优雅降级
- 所有函数返回统一的 (signals, channels, sample_rate) 三元组
- signals: list[np.ndarray]，每个 ndarray 是一个通道的信号
- channels: list[str]，通道名列表
- sample_rate: int，采样率 Hz
"""

from __future__ import annotations

import csv
import io
import logging
import os
import time
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# ============================================================
# 默认配置（可通过环境变量覆盖）
# ============================================================

DEFAULT_SAMPLE_RATE = 256
DEFAULT_CHANNELS = ["TP9", "AF7", "AF8", "TP10"]  # Muse 布局
DEFAULT_ACQUIRE_SECONDS = 4

# 通道名映射：将不同设备的通道名统一到 Muse 布局
# OpenBCI 8 通道默认取前 4 个对应 TP9/AF7/AF8/TP10
# Emotiv 14 通道取 AF3/AF4/TP9/TP10 等
CHANNEL_MAP = {
    # Emotiv EPOC 14 通道 → 取 4 个最接近 Muse 布局的
    "AF3": "AF7", "AF4": "AF8",
    "TP9": "TP9", "TP10": "TP10",
    # OpenBCI 通道编号映射
    "1": "TP9", "2": "AF7", "3": "AF8", "4": "TP10",
    "ch1": "TP9", "ch2": "AF7", "ch3": "AF8", "ch4": "TP10",
}


@dataclass
class DeviceInfo:
    """设备信息（采集时记录，用于结果溯源）"""
    source: str = "unknown"          # lsl / csv / edf / numpy
    device_name: str = ""            # 设备名或文件名
    channels: list[str] = field(default_factory=list)
    sample_rate: int = DEFAULT_SAMPLE_RATE
    duration_seconds: float = 0.0
    signal_quality: str = "unknown"  # good / fair / poor
    quality_detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "device_name": self.device_name,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
            "duration_seconds": round(self.duration_seconds, 2),
            "signal_quality": self.signal_quality,
            "quality_detail": self.quality_detail,
        }


# ============================================================
# LSL 实时流采集（Muse / Emotiv / OpenBCI）
# ============================================================

def acquire_from_lsl(
    stream_name: str = "auto",
    stream_type: str = "EEG",
    duration_seconds: float = DEFAULT_ACQUIRE_SECONDS,
    timeout: float = 10.0,
) -> tuple[list[np.ndarray], list[str], int, DeviceInfo]:
    """从 LSL 流实时采集 EEG 信号。

    支持 Muse（muselsl）、Emotiv（Cortex API LSL 桥）、OpenBCI（GUI LSL 输出）。

    Args:
        stream_name: LSL 流名称，"auto" 表示自动搜索第一个 EEG 流
        stream_type: LSL 流类型（通常为 "EEG"）
        duration_seconds: 采集时长（秒）
        timeout: 搜索流超时（秒）

    Returns:
        (signals, channels, sample_rate, device_info)

    Raises:
        ImportError: pylsl 未安装
        RuntimeError: 未找到 LSL 流或采集失败
    """
    try:
        from pylsl import StreamInlet, resolve_byprop
    except ImportError as e:
        raise ImportError(
            "pylsl 未安装。请运行：pip install pylsl\n"
            "Muse 设备还需：pip install muselsl bleak"
        ) from e

    # 搜索 LSL 流
    logger.info("搜索 LSL 流: name=%s, type=%s", stream_name, stream_type)
    if stream_name == "auto":
        streams = resolve_byprop("type", stream_type, timeout=timeout)
    else:
        streams = resolve_byprop("name", stream_name, timeout=timeout)

    if not streams:
        raise RuntimeError(
            f"未找到 LSL EEG 流（name={stream_name}, type={stream_type}）。\n"
            "请确认设备已连接且 LSL 推流正在运行：\n"
            "  Muse:    muselsl stream\n"
            "  OpenBCI: OpenBCI GUI → Networking → LSL Stream → Start\n"
            "  Emotiv:  Emotiv PRO → LSL Bridge → Start"
        )

    inlet = StreamInlet(streams[0])
    info = streams[0]
    device_name = info.name()
    channels = info.channel_labels() or [f"ch{i+1}" for i in range(info.channel_count())]
    sample_rate = int(info.nominal_srate()) or DEFAULT_SAMPLE_RATE

    logger.info("已连接 LSL 流: %s (%d 通道, %d Hz)", device_name, len(channels), sample_rate)

    # 采集数据
    signals = [[] for _ in range(info.channel_count())]
    start_time = time.time()
    while time.time() - start_time < duration_seconds:
        sample, _timestamp = inlet.pull_sample(timeout=1.0)
        if sample:
            for i, val in enumerate(sample):
                if i < len(signals):
                    signals[i].append(float(val))

    # 转为 numpy 数组
    signals_np = [np.array(ch, dtype=np.float64) for ch in signals]

    # 通道名映射到 Muse 布局（取前 4 通道）
    channels_mapped, signals_mapped = _map_channels(channels, signals_np)

    # 信号质量评估
    quality, detail = _assess_quality(signals_mapped, sample_rate)

    device_info = DeviceInfo(
        source="lsl",
        device_name=device_name,
        channels=channels_mapped,
        sample_rate=sample_rate,
        duration_seconds=duration_seconds,
        signal_quality=quality,
        quality_detail=detail,
    )

    logger.info("LSL 采集完成: %d 通道 × %d 采样点, 质量=%s",
                len(signals_mapped), len(signals_mapped[0]) if signals_mapped else 0, quality)

    return signals_mapped, channels_mapped, sample_rate, device_info


def check_lsl_connection() -> dict:
    """检查 LSL 设备连接状态（不采集，仅探测）。

    Returns:
        连接状态字典
    """
    try:
        from pylsl import resolve_byprop
    except ImportError:
        return {
            "connected": False,
            "error": "pylsl 未安装",
            "hint": "运行 pip install pylsl muselsl bleak",
        }

    try:
        streams = resolve_byprop("type", "EEG", timeout=5.0)
    except Exception as e:
        return {"connected": False, "error": str(e)}

    if not streams:
        return {
            "connected": False,
            "error": "未找到 EEG 流",
            "hint": "请先启动设备 LSL 推流（如 muselsl stream）",
        }

    info = streams[0]
    return {
        "connected": True,
        "device_name": info.name(),
        "channels": info.channel_labels() or [f"ch{i+1}" for i in range(info.channel_count())],
        "sample_rate": int(info.nominal_srate()) or DEFAULT_SAMPLE_RATE,
        "channel_count": info.channel_count(),
    }


# ============================================================
# CSV 文件导入
# ============================================================

def load_from_csv(
    file_content: bytes | str,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    filename: str = "",
) -> tuple[list[np.ndarray], list[str], int, DeviceInfo]:
    """从 CSV 文件加载 EEG 信号。

    格式要求：
    - 第一行：通道名（逗号分隔），如 TP9,AF7,AF8,TP10
    - 第二行起：每行一个采样点的各通道电压值（微伏 μV）

    Args:
        file_content: 文件内容（字节或字符串）
        sample_rate: 采样率（Hz）
        filename: 文件名（用于记录）

    Returns:
        (signals, channels, sample_rate, device_info)
    """
    # 兼容 BOM：字节内容需先解码
    text = file_content.decode("utf-8-sig") if isinstance(file_content, bytes) else file_content

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        raise ValueError("CSV 文件至少需要 1 行表头 + 1 行数据")

    # 第一行是通道名
    channels_raw = [c.strip() for c in rows[0] if c.strip()]
    if not channels_raw:
        raise ValueError("CSV 第一行必须包含通道名")

    # 解析数据行
    data_rows = []
    for row in rows[1:]:
        vals = []
        for cell in row[:len(channels_raw)]:
            try:
                vals.append(float(cell))
            except (ValueError, TypeError):
                vals.append(0.0)
        if vals:
            data_rows.append(vals)

    if not data_rows:
        raise ValueError("CSV 文件无有效数据行")

    # 转为 numpy（每列一个通道）
    data = np.array(data_rows, dtype=np.float64)  # shape: (n_samples, n_channels)
    signals_np = [data[:, i] for i in range(data.shape[1])]

    # 通道名映射
    channels_mapped, signals_mapped = _map_channels(channels_raw, signals_np)

    # 信号质量评估
    quality, detail = _assess_quality(signals_mapped, sample_rate)

    duration = len(data_rows) / sample_rate
    device_info = DeviceInfo(
        source="csv",
        device_name=filename,
        channels=channels_mapped,
        sample_rate=sample_rate,
        duration_seconds=duration,
        signal_quality=quality,
        quality_detail=detail,
    )

    logger.info("CSV 导入完成: %s (%d 通道 × %d 点, %.1fs, 质量=%s)",
                filename, len(signals_mapped), len(data_rows), duration, quality)

    return signals_mapped, channels_mapped, sample_rate, device_info


# ============================================================
# EDF 文件导入（临床标准格式）
# ============================================================

def load_from_edf(
    file_path: str,
    target_channels: list[str] = None,
) -> tuple[list[np.ndarray], list[str], int, DeviceInfo]:
    """从 EDF 文件加载 EEG 信号。

    需要安装 pyedflib：pip install pyedflib

    Args:
        file_path: EDF 文件路径
        target_channels: 目标通道名（只加载这些通道，None 表示加载全部）

    Returns:
        (signals, channels, sample_rate, device_info)
    """
    try:
        import pyedflib
    except ImportError as e:
        raise ImportError(
            "pyedflib 未安装。请运行：pip install pyedflib"
        ) from e

    try:
        reader = pyedflib.EdfReader(file_path)
    except Exception as e:
        raise ValueError(f"无法读取 EDF 文件: {e}") from e

    try:
        n_channels = reader.signals_in_file
        channel_labels = reader.getSignalLabels()
        all_signals = []
        all_rates = []

        for i in range(n_channels):
            rate = int(reader.getSampleFrequency(i))
            all_rates.append(rate)
            sig = reader.readSignal(i).astype(np.float64)
            all_signals.append(sig)

        # 统一采样率（取第一个通道的）
        sample_rate = all_rates[0] if all_rates else DEFAULT_SAMPLE_RATE

        # 通道筛选
        if target_channels:
            selected = []
            selected_names = []
            for tc in target_channels:
                for i, label in enumerate(channel_labels):
                    if tc.lower() in str(label).lower():
                        selected.append(all_signals[i])
                        selected_names.append(tc)
                        break
            if selected:
                signals_np = selected
                channels_raw = selected_names
            else:
                signals_np = all_signals
                channels_raw = list(channel_labels)
        else:
            signals_np = all_signals
            channels_raw = list(channel_labels)

        # 通道名映射 + 质量评估
        channels_mapped, signals_mapped = _map_channels(channels_raw, signals_np)
        quality, detail = _assess_quality(signals_mapped, sample_rate)

        duration = len(signals_mapped[0]) / sample_rate if signals_mapped else 0
        device_info = DeviceInfo(
            source="edf",
            device_name=os.path.basename(file_path),
            channels=channels_mapped,
            sample_rate=sample_rate,
            duration_seconds=duration,
            signal_quality=quality,
            quality_detail=detail,
        )

        logger.info("EDF 导入完成: %s (%d 通道 × %d 点, %.1fs)",
                    os.path.basename(file_path), len(signals_mapped),
                    len(signals_mapped[0]) if signals_mapped else 0, duration)

        return signals_mapped, channels_mapped, sample_rate, device_info

    finally:
        reader.close()


# ============================================================
# NumPy 数组接口（编程用，测试和集成）
# ============================================================

def from_numpy(
    signals: list[np.ndarray],
    channels: list[str] = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> tuple[list[np.ndarray], list[str], int, DeviceInfo]:
    """从 NumPy 数组创建 EEG 信号（编程接口）。

    用于直接传入已采集的信号数据，跳过文件/设备读取。

    Args:
        signals: 多通道信号列表，每个元素是一个通道的 ndarray
        channels: 通道名列表（None 则用默认 Muse 布局）
        sample_rate: 采样率

    Returns:
        (signals, channels, sample_rate, device_info)
    """
    if not signals:
        raise ValueError("signals 不能为空")

    if channels is None:
        channels = DEFAULT_CHANNELS[:len(signals)]

    channels_mapped, signals_mapped = _map_channels(channels, signals)
    quality, detail = _assess_quality(signals_mapped, sample_rate)

    duration = len(signals_mapped[0]) / sample_rate if signals_mapped else 0
    device_info = DeviceInfo(
        source="numpy",
        device_name="direct_input",
        channels=channels_mapped,
        sample_rate=sample_rate,
        duration_seconds=duration,
        signal_quality=quality,
        quality_detail=detail,
    )

    return signals_mapped, channels_mapped, sample_rate, device_info


# ============================================================
# 内部工具函数
# ============================================================

def _map_channels(
    channels: list[str],
    signals: list[np.ndarray],
    target: list[str] = None,
) -> tuple[list[str], list[np.ndarray]]:
    """将设备通道映射到 Muse 4 通道布局。

    策略：
    1. 如果通道名已在 CHANNEL_MAP 中，映射到 Muse 布局
    2. 如果通道数 > 4，取前 4 个
    3. 如果通道数 <= 4，保留原通道名（但尽量映射）
    """
    if target is None:
        target = DEFAULT_CHANNELS

    # 尝试按通道名映射
    mapped = []
    for ch in channels:
        mapped.append(CHANNEL_MAP.get(ch, CHANNEL_MAP.get(ch.lower(), ch)))

    # 如果映射后包含 Muse 通道名，优先取这些
    muse_channels = []
    muse_signals = []
    for i, ch in enumerate(mapped):
        if ch in target and ch not in muse_channels:
            muse_channels.append(ch)
            muse_signals.append(signals[i])

    # 如果找到了 Muse 布局通道，返回它们
    if len(muse_channels) >= 4:
        return muse_channels[:4], muse_signals[:4]

    # 否则取前 4 个通道，用 Muse 布局命名
    n = min(4, len(signals))
    return target[:n], signals[:n]


def _assess_quality(signals: list[np.ndarray], sample_rate: int) -> tuple[str, dict]:
    """评估信号质量。

    判定依据：
    - 50Hz 工频干扰占比（FFT 分析）
    - 信号幅度范围（正常 EEG 10-100μV）
    - 信号方差（过低可能是接触不良）

    Returns:
        (quality, detail)
        quality: "good" / "fair" / "poor"
        detail: 详细指标
    """
    if not signals or len(signals[0]) == 0:
        return "poor", {"error": "空信号"}

    # 取第一个通道评估
    sig = signals[0]
    n = len(sig)

    # 1. 信号幅度
    amplitude = float(np.ptp(sig))  # 峰峰值
    rms = float(np.sqrt(np.mean(sig ** 2)))

    # 2. 50Hz 工频干扰占比（FFT）
    if n >= 256:
        fft = np.abs(np.fft.rfft(sig))
        freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
        total_power = np.sum(fft) + 1e-10
        # 50Hz 附近功率（48-52Hz）
        mask_50 = (freqs >= 48) & (freqs <= 52)
        power_50 = np.sum(fft[mask_50])
        ratio_50 = float(power_50 / total_power)
    else:
        ratio_50 = 0.0

    # 3. 信号方差
    variance = float(np.var(sig))

    # 综合判定
    detail = {
        "amplitude_uv": round(amplitude, 2),
        "rms_uv": round(rms, 2),
        "powerline_50hz_ratio": round(ratio_50, 4),
        "variance": round(variance, 2),
    }

    if ratio_50 > 0.3 or amplitude > 500 or variance < 1.0:
        return "poor", detail
    elif ratio_50 > 0.1 or amplitude > 200 or variance < 5.0:
        return "fair", detail
    else:
        return "good", detail


# ============================================================
# 配置读取
# ============================================================

def get_device_config() -> dict:
    """从环境变量读取设备配置。"""
    return {
        "device_type": os.getenv("EEG_DEVICE_TYPE", "synthetic"),  # synthetic / muse_lsl / openbci_lsl / emotiv_lsl
        "lsl_stream_name": os.getenv("EEG_LSL_STREAM_NAME", "auto"),
        "lsl_stream_type": os.getenv("EEG_LSL_STREAM_TYPE", "EEG"),
        "acquire_seconds": float(os.getenv("EEG_ACQUIRE_SECONDS", str(DEFAULT_ACQUIRE_SECONDS))),
    }
