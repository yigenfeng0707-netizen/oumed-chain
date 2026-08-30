"""EEG 设备适配层（device_adapter.py）单元测试

覆盖：
- DeviceInfo 数据类序列化
- CSV 文件导入（合法/异常格式）
- NumPy 数组接口（编程式接入）
- 通道名映射（Emotiv 14ch / OpenBCI 8ch → Muse 4ch）
- 信号质量评估（good/fair/poor 三档）
- LSL 接口在 pylsl 未安装时的优雅降级
- EDF 接口在 pyedflib 未安装时的优雅降级
- assess_real_session 端到端：真实信号 → 频域 → 指标 → 预警 → 政策联动
- 自动心理状态推断（α/β/θ/δ 比值规则）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pytest

from app.services.eeg import device_adapter as da
from app.services.eeg import engine as eeg

# ============================================================
# 1. DeviceInfo 数据类
# ============================================================

class TestDeviceInfo:
    """DeviceInfo 数据类测试"""

    def test_default_values(self):
        """默认值"""
        info = da.DeviceInfo()
        assert info.source == "unknown"
        assert info.signal_quality == "unknown"
        assert info.sample_rate == da.DEFAULT_SAMPLE_RATE
        assert info.channels == []

    def test_to_dict_keys(self):
        """to_dict 包含所有字段"""
        info = da.DeviceInfo(
            source="lsl",
            device_name="Muse-1234",
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            duration_seconds=4.0,
            signal_quality="good",
            quality_detail={"amplitude_uv": 50.0},
        )
        d = info.to_dict()
        for key in ("source", "device_name", "channels", "sample_rate",
                    "duration_seconds", "signal_quality", "quality_detail"):
            assert key in d
        assert d["source"] == "lsl"
        assert d["signal_quality"] == "good"


# ============================================================
# 2. CSV 文件导入
# ============================================================

class TestCsvImport:
    """CSV 文件导入测试"""

    def _make_csv(self, channels: list[str], n_samples: int, sample_rate: int = 256) -> str:
        """生成测试 CSV 内容"""
        lines = [",".join(channels)]
        rng = np.random.default_rng(42)
        for _ in range(n_samples):
            # 生成 10-50μV 的模拟 EEG 信号
            vals = rng.normal(0, 20, size=len(channels))
            lines.append(",".join(f"{v:.4f}" for v in vals))
        return "\n".join(lines)

    def test_basic_csv_import(self):
        """基本 CSV 导入：4 通道 × 1024 采样点"""
        csv_content = self._make_csv(["TP9", "AF7", "AF8", "TP10"], 1024)
        signals, channels, sr, info = da.load_from_csv(
            csv_content, sample_rate=256, filename="test.csv"
        )
        assert len(signals) == 4
        assert channels == ["TP9", "AF7", "AF8", "TP10"]
        assert sr == 256
        assert len(signals[0]) == 1024
        assert info.source == "csv"
        assert info.device_name == "test.csv"
        assert info.duration_seconds == 4.0  # 1024 / 256

    def test_csv_with_bytes_input(self):
        """字节数据输入（兼容 BOM）"""
        csv_content = self._make_csv(["TP9", "AF7", "AF8", "TP10"], 256)
        signals, _, _, info = da.load_from_csv(
            csv_content.encode("utf-8-sig"), sample_rate=256
        )
        assert len(signals) == 4
        assert info.source == "csv"

    def test_csv_emotiv_14ch_mapped_to_muse(self):
        """Emotiv 14 通道 CSV 映射到 Muse 4 通道"""
        emotiv_channels = [
            "AF3", "F7", "F3", "FC5", "T7", "P7", "O1",
            "O2", "P8", "T8", "FC6", "F4", "F8", "AF4"
        ]
        csv_content = self._make_csv(emotiv_channels, 256)
        signals, channels, _, info = da.load_from_csv(csv_content, sample_rate=256)
        # 映射后应该是 4 通道 Muse 布局
        assert len(signals) == 4
        assert channels == ["TP9", "AF7", "AF8", "TP10"]
        assert info.channels == ["TP9", "AF7", "AF8", "TP10"]

    def test_csv_openbci_8ch_mapped_to_muse(self):
        """OpenBCI 8 通道（数字命名）映射到 Muse 4 通道"""
        openbci_channels = ["1", "2", "3", "4", "5", "6", "7", "8"]
        csv_content = self._make_csv(openbci_channels, 256)
        signals, channels, _, _ = da.load_from_csv(csv_content, sample_rate=256)
        assert len(signals) == 4
        assert channels == ["TP9", "AF7", "AF8", "TP10"]

    def test_csv_too_short_raises(self):
        """CSV 行数不足抛出 ValueError"""
        with pytest.raises(ValueError, match="至少需要"):
            da.load_from_csv("TP9,AF7,AF8,TP10", sample_rate=256)

    def test_csv_empty_raises(self):
        """空 CSV 抛出 ValueError"""
        with pytest.raises(ValueError):
            da.load_from_csv("", sample_rate=256)

    def test_csv_invalid_values_replaced_with_zero(self):
        """无效数值被替换为 0"""
        csv_content = "TP9,AF7\n10.0,20.0\nabc,30.0\n40.0,xyz"
        signals, _, _, _ = da.load_from_csv(csv_content, sample_rate=256)
        # 第二行 abc → 0（TP9 通道第二个采样点）
        assert signals[0][1] == 0.0
        # 第二行 xyz → 0（AF7 通道第二个采样点）
        assert signals[1][2] == 0.0

    def test_csv_quality_assessment(self):
        """CSV 导入包含信号质量评估"""
        csv_content = self._make_csv(["TP9", "AF7", "AF8", "TP10"], 1024)
        _, _, _, info = da.load_from_csv(csv_content, sample_rate=256)
        assert info.signal_quality in ("good", "fair", "poor")
        assert "amplitude_uv" in info.quality_detail
        assert "rms_uv" in info.quality_detail
        assert "powerline_50hz_ratio" in info.quality_detail


# ============================================================
# 3. NumPy 数组接口
# ============================================================

class TestNumpyInterface:
    """NumPy 编程接口测试"""

    def test_basic_numpy_input(self):
        """基本 NumPy 输入"""
        rng = np.random.default_rng(42)
        signals = [rng.normal(0, 20, 1024) for _ in range(4)]
        result_signals, channels, sr, info = da.from_numpy(
            signals, sample_rate=256
        )
        assert len(result_signals) == 4
        assert channels == ["TP9", "AF7", "AF8", "TP10"]
        assert sr == 256
        assert info.source == "numpy"
        assert info.duration_seconds == 4.0

    def test_numpy_custom_channels(self):
        """自定义通道名（映射后保留输入顺序）"""
        rng = np.random.default_rng(42)
        signals = [rng.normal(0, 20, 256) for _ in range(4)]
        _, channels, _, _ = da.from_numpy(
            signals, channels=["AF3", "AF4", "TP9", "TP10"], sample_rate=256
        )
        # AF3 → AF7, AF4 → AF8, TP9/TP10 保留，顺序与输入一致
        assert channels == ["AF7", "AF8", "TP9", "TP10"]

    def test_numpy_empty_raises(self):
        """空信号抛出 ValueError"""
        with pytest.raises(ValueError, match="不能为空"):
            da.from_numpy([], sample_rate=256)

    def test_numpy_two_channels_padded(self):
        """2 通道信号：取前 2 个 Muse 通道"""
        rng = np.random.default_rng(42)
        signals = [rng.normal(0, 20, 256) for _ in range(2)]
        _, channels, _, _ = da.from_numpy(signals, sample_rate=256)
        assert len(channels) == 2
        assert channels == ["TP9", "AF7"]


# ============================================================
# 4. 通道名映射
# ============================================================

class TestChannelMapping:
    """通道名映射测试"""

    def test_emotiv_af3_af4_mapped(self):
        """Emotiv AF3/AF4 映射到 AF7/AF8"""
        channels = ["AF3", "AF4", "TP9", "TP10"]
        signals = [np.zeros(64) for _ in range(4)]
        mapped_ch, mapped_sig = da._map_channels(channels, signals)
        assert "AF7" in mapped_ch
        assert "AF8" in mapped_ch
        assert "TP9" in mapped_ch
        assert "TP10" in mapped_ch

    def test_openbci_numeric_channels(self):
        """OpenBCI 数字通道名映射"""
        channels = ["1", "2", "3", "4"]
        signals = [np.zeros(64) for _ in range(4)]
        mapped_ch, _ = da._map_channels(channels, signals)
        assert mapped_ch == ["TP9", "AF7", "AF8", "TP10"]

    def test_more_than_four_channels_truncated(self):
        """超过 4 通道截取前 4 个"""
        channels = ["ch1", "ch2", "ch3", "ch4", "ch5", "ch6"]
        signals = [np.zeros(64) for _ in range(6)]
        mapped_ch, mapped_sig = da._map_channels(channels, signals)
        assert len(mapped_ch) == 4
        assert len(mapped_sig) == 4

    def test_unknown_channels_use_muse_layout(self):
        """未知通道名使用 Muse 布局命名"""
        channels = ["foo", "bar", "baz", "qux"]
        signals = [np.zeros(64) for _ in range(4)]
        mapped_ch, _ = da._map_channels(channels, signals)
        # 未知通道名 → 取前 4 个用 Muse 布局命名
        assert mapped_ch == ["TP9", "AF7", "AF8", "TP10"]


# ============================================================
# 5. 信号质量评估
# ============================================================

class TestQualityAssessment:
    """信号质量评估测试"""

    def test_good_quality_signal(self):
        """正常 EEG 信号 → good"""
        rng = np.random.default_rng(42)
        # 20μV 标准差，无 50Hz 干扰
        signals = [rng.normal(0, 20, 1024) for _ in range(4)]
        quality, detail = da._assess_quality(signals, 256)
        assert quality in ("good", "fair")  # 随机信号可能 fair
        assert "amplitude_uv" in detail

    def test_poor_quality_high_amplitude(self):
        """幅度过大 → poor"""
        rng = np.random.default_rng(42)
        signals = [rng.normal(0, 500, 1024) for _ in range(4)]  # 500μV 远超正常
        quality, detail = da._assess_quality(signals, 256)
        assert quality == "poor"
        assert detail["amplitude_uv"] > 200

    def test_poor_quality_low_variance(self):
        """方差过低（接触不良）→ poor"""
        signals = [np.full(1024, 0.1) for _ in range(4)]  # 几乎为常数
        quality, detail = da._assess_quality(signals, 256)
        assert quality == "poor"
        assert detail["variance"] < 1.0

    def test_empty_signal_returns_poor(self):
        """空信号 → poor"""
        quality, detail = da._assess_quality([], 256)
        assert quality == "poor"
        assert "error" in detail

    def test_short_signal_no_fft(self):
        """短信号（<256 点）跳过 FFT 分析"""
        signals = [np.array([10.0, 20.0, 30.0, 40.0])]
        quality, detail = da._assess_quality(signals, 256)
        assert detail["powerline_50hz_ratio"] == 0.0


# ============================================================
# 6. LSL / EDF 优雅降级
# ============================================================

class TestOptionalDependencies:
    """可选依赖未安装时的优雅降级"""

    def test_lsl_check_without_pylsl(self):
        """未安装 pylsl 时 check_lsl_connection 返回错误信息"""
        # 注意：如果测试环境装了 pylsl，这个测试会跳过
        try:
            import pylsl  # noqa: F401
            pytest.skip("pylsl 已安装，跳过降级测试")
        except ImportError:
            pass

        result = da.check_lsl_connection()
        assert result["connected"] is False
        assert "pylsl" in result.get("error", "") or "pylsl" in result.get("hint", "")

    def test_lsl_acquire_without_pylsl_raises(self):
        """未安装 pylsl 时 acquire_from_lsl 抛出 ImportError"""
        try:
            import pylsl  # noqa: F401
            pytest.skip("pylsl 已安装，跳过降级测试")
        except ImportError:
            pass

        with pytest.raises(ImportError, match="pylsl"):
            da.acquire_from_lsl(duration_seconds=0.1)

    def test_edf_load_without_pyedflib_raises(self):
        """未安装 pyedflib 时 load_from_edf 抛出 ImportError"""
        try:
            import pyedflib  # noqa: F401
            pytest.skip("pyedflib 已安装，跳过降级测试")
        except ImportError:
            pass

        with pytest.raises(ImportError, match="pyedflib"):
            da.load_from_edf("/tmp/nonexistent.edf")


# ============================================================
# 7. 配置读取
# ============================================================

class TestDeviceConfig:
    """设备配置读取测试"""

    def test_default_config(self):
        """默认配置"""
        # 清除环境变量
        for key in ("EEG_DEVICE_TYPE", "EEG_LSL_STREAM_NAME", "EEG_LSL_STREAM_TYPE", "EEG_ACQUIRE_SECONDS"):
            os.environ.pop(key, None)
        cfg = da.get_device_config()
        assert cfg["device_type"] == "synthetic"
        assert cfg["lsl_stream_name"] == "auto"
        assert cfg["lsl_stream_type"] == "EEG"
        assert cfg["acquire_seconds"] == 4.0

    def test_env_override(self):
        """环境变量覆盖"""
        os.environ["EEG_DEVICE_TYPE"] = "muse_lsl"
        os.environ["EEG_LSL_STREAM_NAME"] = "Muse-1234"
        os.environ["EEG_ACQUIRE_SECONDS"] = "8"
        try:
            cfg = da.get_device_config()
            assert cfg["device_type"] == "muse_lsl"
            assert cfg["lsl_stream_name"] == "Muse-1234"
            assert cfg["acquire_seconds"] == 8.0
        finally:
            os.environ.pop("EEG_DEVICE_TYPE")
            os.environ.pop("EEG_LSL_STREAM_NAME")
            os.environ.pop("EEG_ACQUIRE_SECONDS")


# ============================================================
# 8. assess_real_session 端到端（核心创新）
# ============================================================

class TestAssessRealSession:
    """真实信号评估端到端测试"""

    def _generate_relaxed_signal(self, n_samples: int = 1024, sr: int = 256) -> list[np.ndarray]:
        """生成模拟放松状态信号（高 α 波）"""
        t = np.arange(n_samples) / sr
        rng = np.random.default_rng(42)
        signals = []
        for _ in range(4):
            # α 波 10Hz + 少量 β 波 20Hz + 噪声
            alpha = 30 * np.sin(2 * np.pi * 10 * t)
            beta = 5 * np.sin(2 * np.pi * 20 * t)
            noise = rng.normal(0, 5, n_samples)
            signals.append(alpha + beta + noise)
        return signals

    def _generate_stressed_signal(self, n_samples: int = 1024, sr: int = 256) -> list[np.ndarray]:
        """生成模拟高压力状态信号（高 β 波）"""
        t = np.arange(n_samples) / sr
        rng = np.random.default_rng(42)
        signals = []
        for _ in range(4):
            alpha = 5 * np.sin(2 * np.pi * 10 * t)
            beta = 30 * np.sin(2 * np.pi * 20 * t)
            noise = rng.normal(0, 5, n_samples)
            signals.append(alpha + beta + noise)
        return signals

    def test_basic_real_session(self):
        """基本真实信号评估"""
        signals = self._generate_relaxed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
        )
        assert session.user_id == "1"
        assert session.channels == ["TP9", "AF7", "AF8", "TP10"]
        assert session.sample_rate == 256
        assert session.source == "device"
        assert len(session.waveform) == 4
        # 指标在合理范围
        for key in ("stress_index", "attention_index", "sleep_quality", "cognitive_load"):
            assert 0 <= session.metrics[key] <= 100

    def test_auto_state_inference_relaxed(self):
        """auto 模式：高 α 波 → 推断为 relaxed"""
        signals = self._generate_relaxed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            mental_state="auto",
        )
        # α 波占主导，应推断为 relaxed
        assert session.mental_state == "relaxed"
        assert session.mental_state_label == "放松"

    def test_auto_state_inference_stressed(self):
        """auto 模式：高 β 波 → 推断为 stressed"""
        signals = self._generate_stressed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            mental_state="auto",
        )
        assert session.mental_state == "stressed"

    def test_explicit_state_label(self):
        """显式指定心理状态：保留标签"""
        signals = self._generate_relaxed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            mental_state="focused",
        )
        assert session.mental_state == "focused"
        assert session.mental_state_label == "专注"

    def test_device_info_attached(self):
        """设备信息附加到会话"""
        signals = self._generate_relaxed_signal()
        device_info = {
            "source": "lsl",
            "device_name": "Muse-1234",
            "signal_quality": "good",
        }
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            device_info=device_info,
        )
        assert session.source == "device"
        assert session.device_info["device_name"] == "Muse-1234"

    def test_file_source_marked(self):
        """文件来源标记为 file"""
        signals = self._generate_relaxed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            device_info={"source": "file", "device_name": "test.csv"},
        )
        assert session.source == "file"

    def test_stressed_signal_triggers_alerts(self):
        """高压力信号触发预警"""
        signals = self._generate_stressed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            mental_state="stressed",
        )
        # 高压力状态应触发预警
        assert len(session.alerts) > 0

    def test_to_dict_includes_source_and_device_info(self):
        """to_dict 包含 source 和 device_info 字段"""
        signals = self._generate_relaxed_signal()
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=["TP9", "AF7", "AF8", "TP10"],
            sample_rate=256,
            device_info={"source": "lsl", "device_name": "Muse"},
        )
        d = session.to_dict()
        assert "source" in d
        assert "device_info" in d
        assert d["source"] == "device"

    def test_csv_to_session_pipeline(self):
        """完整管道：CSV → device_adapter → assess_real_session"""
        # 1. 生成 CSV 内容
        rng = np.random.default_rng(42)
        t = np.arange(1024) / 256
        lines = ["TP9,AF7,AF8,TP10"]
        for i in range(1024):
            vals = [
                30 * np.sin(2 * np.pi * 10 * t[i]) + rng.normal(0, 5),
                30 * np.sin(2 * np.pi * 10 * t[i]) + rng.normal(0, 5),
                30 * np.sin(2 * np.pi * 10 * t[i]) + rng.normal(0, 5),
                30 * np.sin(2 * np.pi * 10 * t[i]) + rng.normal(0, 5),
            ]
            lines.append(",".join(f"{v:.4f}" for v in vals))
        csv_content = "\n".join(lines)

        # 2. 通过 device_adapter 加载
        signals, channels, sr, info = da.load_from_csv(csv_content, sample_rate=256, filename="test.csv")

        # 3. 喂入 assess_real_session
        session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=channels,
            sample_rate=sr,
            device_info=info.to_dict(),
        )
        assert session.source == "file"  # CSV 来源标记为 file
        assert session.sample_rate == 256
        assert len(session.waveform) == 4
        assert session.device_info["source"] == "csv"


# ============================================================
# 9. 集成验证：合成信号 vs 真实信号路径一致性
# ============================================================

class TestPipelineConsistency:
    """合成信号路径与真实信号路径结果一致性测试"""

    def test_both_paths_produce_valid_metrics(self):
        """两条路径都产生有效指标"""
        # 合成路径
        synthetic_session = eeg.assess_session(user_id="1", mental_state="relaxed", seed=42)

        # 真实路径（用合成信号作为"真实"输入）
        signals, channels, sr = eeg.generate_synthetic_eeg(mental_state="relaxed", seed=42)
        real_session = eeg.assess_real_session(
            user_id="1",
            signals=signals,
            channels=channels,
            sample_rate=sr,
            mental_state="relaxed",
        )

        # 两者都应产生有效的 4 维指标
        for key in ("stress_index", "attention_index", "sleep_quality", "cognitive_load"):
            assert 0 <= synthetic_session.metrics[key] <= 100
            assert 0 <= real_session.metrics[key] <= 100

    def test_both_paths_produce_waveform(self):
        """两条路径都产生降采样波形"""
        synthetic_session = eeg.assess_session(user_id="1", seed=42)
        signals, channels, sr = eeg.generate_synthetic_eeg(seed=42)
        real_session = eeg.assess_real_session(
            user_id="1", signals=signals, channels=channels, sample_rate=sr
        )
        assert len(synthetic_session.waveform) == 4
        assert len(real_session.waveform) == 4
        for ch_wave in real_session.waveform:
            assert len(ch_wave["data"]) <= 128
