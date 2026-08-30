"""MedSignal - 脑电健康引擎（EEG Engine）

BCI×医保创新核心模块，提供 EEG 信号生成、频域特征提取、
健康指标计算、异常预警、医保政策联动全链路能力。

v2.2 新增：真实设备接入（LSL/CSV/EDF），见 device_adapter 模块。
"""

from app.services.eeg.device_adapter import (
    DeviceInfo,
    acquire_from_lsl,
    check_lsl_connection,
    from_numpy,
    get_device_config,
    load_from_csv,
    load_from_edf,
)
from app.services.eeg.engine import (
    BANDS,
    CHANNELS,
    MENTAL_STATES,
    SAMPLE_RATE,
    EEGSession,
    assess_real_session,
    assess_session,
    compute_health_metrics,
    extract_band_powers,
    generate_synthetic_eeg,
    link_to_policies,
    pick_mental_state_by_profile,
    realtime_stream,
    scan_eeg_alerts,
)

__all__ = [
    # 引擎核心
    "EEGSession",
    "MENTAL_STATES",
    "SAMPLE_RATE",
    "CHANNELS",
    "BANDS",
    "assess_session",
    "assess_real_session",
    "compute_health_metrics",
    "extract_band_powers",
    "generate_synthetic_eeg",
    "link_to_policies",
    "pick_mental_state_by_profile",
    "realtime_stream",
    "scan_eeg_alerts",
    # 设备适配
    "DeviceInfo",
    "acquire_from_lsl",
    "check_lsl_connection",
    "from_numpy",
    "get_device_config",
    "load_from_csv",
    "load_from_edf",
]
