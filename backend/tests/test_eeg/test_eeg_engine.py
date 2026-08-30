"""脑电健康引擎单元测试（BCI×医保创新 v2.1.0）

覆盖：
- 合成 EEG 信号生成（4 通道 / 256Hz / 5 种心理状态）
- 频域特征提取（Welch PSD + 五频段功率积分）
- 健康指标计算（压力/注意力/睡眠/认知负荷/情绪）
- 脑电异常预警（5 条规则 + evidence）
- 脑电异常 → 医保政策联动
- 完整会话评估主入口
- 实时数据块生成
- 用户画像推荐心理状态
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np

from app.services.eeg import engine as eeg

# ============================================================
# 1. 常量与配置
# ============================================================

class TestConstants:
    """引擎常量配置测试"""

    def test_sample_rate_standard(self):
        """采样率为消费级 EEG 标准值"""
        assert eeg.SAMPLE_RATE == 256

    def test_channels_muse_layout(self):
        """4 通道 Muse 布局"""
        assert eeg.CHANNELS == ["TP9", "AF7", "AF8", "TP10"]
        assert len(eeg.CHANNELS) == 4

    def test_bands_five_ranges(self):
        """五频段定义且范围递增"""
        assert set(eeg.BANDS.keys()) == {"delta", "theta", "alpha", "beta", "gamma"}
        prev_hi = 0
        for band, (lo, hi) in eeg.BANDS.items():
            assert lo < hi
            assert lo >= prev_hi
            prev_hi = hi

    def test_mental_states_five_presets(self):
        """5 种心理状态预设"""
        assert set(eeg.MENTAL_STATES.keys()) == {
            "relaxed", "focused", "stressed", "fatigued", "sleep_deprived"
        }
        for key, meta in eeg.MENTAL_STATES.items():
            assert "label" in meta
            assert "stress" in meta
            assert "attention" in meta
            assert "sleep" in meta
            assert "cognitive" in meta


# ============================================================
# 2. 合成 EEG 信号生成
# ============================================================

class TestSignalGeneration:
    """合成 EEG 信号生成测试"""

    def test_default_signal_shape(self):
        """默认参数生成 4 通道信号"""
        signals, channels, sr = eeg.generate_synthetic_eeg(seed=42)
        assert len(signals) == 4
        assert channels == eeg.CHANNELS
        assert sr == eeg.SAMPLE_RATE
        expected_len = eeg.WINDOW_SECONDS * eeg.SAMPLE_RATE
        for s in signals:
            assert len(s) == expected_len

    def test_custom_duration(self):
        """自定义采集时长"""
        signals, _, _ = eeg.generate_synthetic_eeg(duration_seconds=8, seed=42)
        assert len(signals[0]) == 8 * eeg.SAMPLE_RATE

    def test_reproducible_with_seed(self):
        """相同种子可复现"""
        s1, _, _ = eeg.generate_synthetic_eeg(seed=123)
        s2, _, _ = eeg.generate_synthetic_eeg(seed=123)
        for a, b in zip(s1, s2):
            assert np.allclose(a, b)

    def test_different_seed_different_signal(self):
        """不同种子生成不同信号"""
        s1, _, _ = eeg.generate_synthetic_eeg(seed=1)
        s2, _, _ = eeg.generate_synthetic_eeg(seed=2)
        assert not np.allclose(s1[0], s2[0])

    def test_invalid_state_fallback_relaxed(self):
        """无效心理状态回退到 relaxed"""
        s1, _, _ = eeg.generate_synthetic_eeg(mental_state="invalid", seed=42)
        s2, _, _ = eeg.generate_synthetic_eeg(mental_state="relaxed", seed=42)
        for a, b in zip(s1, s2):
            assert np.allclose(a, b)

    def test_all_states_produce_valid_signal(self):
        """所有心理状态都能生成有效信号"""
        for state in eeg.MENTAL_STATES:
            signals, _, _ = eeg.generate_synthetic_eeg(mental_state=state, seed=42)
            assert len(signals) == 4
            assert not np.allclose(signals[0], 0)


# ============================================================
# 3. 频域特征提取
# ============================================================

class TestBandPowers:
    """频域特征提取测试"""

    def test_extract_returns_band_and_avg(self):
        """返回各通道频段功率 + 跨通道平均"""
        signals, _, sr = eeg.generate_synthetic_eeg(seed=42)
        band_powers, avg_powers = eeg.extract_band_powers(signals, sr)
        assert set(band_powers.keys()) == set(eeg.CHANNELS)
        for ch in eeg.CHANNELS:
            assert set(band_powers[ch].keys()) == set(eeg.BANDS.keys())
        assert set(avg_powers.keys()) == set(eeg.BANDS.keys())

    def test_powers_non_negative(self):
        """功率值非负"""
        signals, _, sr = eeg.generate_synthetic_eeg(seed=42)
        band_powers, avg_powers = eeg.extract_band_powers(signals, sr)
        for ch, powers in band_powers.items():
            for band, val in powers.items():
                assert val >= 0, f"{ch}.{band} 功率为负: {val}"
        for band, val in avg_powers.items():
            assert val >= 0

    def test_relaxed_high_alpha(self):
        """放松状态 α 波功率应高于 β 波"""
        signals, _, sr = eeg.generate_synthetic_eeg(mental_state="relaxed", seed=42)
        _, avg = eeg.extract_band_powers(signals, sr)
        assert avg["alpha"] > avg["beta"]

    def test_stressed_high_beta(self):
        """高压力状态 β 波功率应高于 α 波"""
        signals, _, sr = eeg.generate_synthetic_eeg(mental_state="stressed", seed=42)
        _, avg = eeg.extract_band_powers(signals, sr)
        assert avg["beta"] > avg["alpha"]

    def test_fatigued_high_delta_theta(self):
        """疲劳状态 δ/θ 波功率占比高于压力状态"""
        fatigued_signals, _, sr = eeg.generate_synthetic_eeg(mental_state="fatigued", seed=42)
        stressed_signals, _, sr2 = eeg.generate_synthetic_eeg(mental_state="stressed", seed=42)
        _, fat_avg = eeg.extract_band_powers(fatigued_signals, sr)
        _, str_avg = eeg.extract_band_powers(stressed_signals, sr2)
        fat_slow = fat_avg["delta"] + fat_avg["theta"]
        str_slow = str_avg["delta"] + str_avg["theta"]
        assert fat_slow > str_slow


# ============================================================
# 4. 健康指标计算
# ============================================================

class TestHealthMetrics:
    """脑电健康指标计算测试"""

    def test_metrics_all_dimensions(self):
        """指标包含 4 维 + 情绪 + 比值 + 赛道7新增指标"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        for key in ("stress_index", "attention_index", "sleep_quality", "cognitive_load"):
            assert key in m
        assert "emotion" in m
        assert "ratios" in m
        # ⭐ 赛道7核心新增指标
        assert "cerebrovascular_risk" in m
        assert "cognitive_decline_risk" in m
        assert "mental_health" in m

    def test_metrics_in_range(self):
        """所有指标在 0-100 范围内"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        for key in ("stress_index", "attention_index", "sleep_quality", "cognitive_load"):
            assert 0 <= m[key] <= 100
        assert 0 <= m["emotion"]["valence"] <= 100
        assert 0 <= m["emotion"]["arousal"] <= 100
        # ⭐ 新增指标范围
        assert 0 <= m["cerebrovascular_risk"] <= 100
        assert 0 <= m["cognitive_decline_risk"] <= 100
        assert 0 <= m["mental_health"]["anxiety_score"] <= 100
        assert 0 <= m["mental_health"]["depression_score"] <= 100
        assert 0 <= m["mental_health"]["overall_risk"] <= 100

    def test_emotion_label_valid(self):
        """情绪标签在预设范围内"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        assert m["emotion"]["label"] in {
            "焦虑倾向", "低落倾向", "积极兴奋", "平静放松", "情绪平稳"
        }

    def test_ratios_computed(self):
        """比值正确计算"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        r = m["ratios"]
        assert abs(r["alpha_beta"] - 15 / 5) < 0.01
        assert abs(r["theta_beta"] - 8 / 5) < 0.01
        # ⭐ 新增比值
        assert "theta_alpha" in r
        assert "delta_ratio" in r

    def test_zero_powers_no_crash(self):
        """全零功率不崩溃"""
        avg = {"delta": 0, "theta": 0, "alpha": 0, "beta": 0, "gamma": 0}
        m = eeg.compute_health_metrics(avg)
        assert 0 <= m["stress_index"] <= 100
        assert 0 <= m["cerebrovascular_risk"] <= 100
        assert 0 <= m["cognitive_decline_risk"] <= 100

    # ⭐ 赛道7新增：脑血管风险指数测试
    def test_cerebrovascular_risk_high_delta(self):
        """δ波激增时脑血管风险升高"""
        avg_high_delta = {"delta": 50, "theta": 8, "alpha": 5, "beta": 3, "gamma": 2}
        avg_normal = {"delta": 10, "theta": 8, "alpha": 30, "beta": 10, "gamma": 5}
        m_high = eeg.compute_health_metrics(avg_high_delta)
        m_normal = eeg.compute_health_metrics(avg_normal)
        assert m_high["cerebrovascular_risk"] > m_normal["cerebrovascular_risk"]

    def test_cerebrovascular_risk_alpha_suppression(self):
        """α波抑制时脑血管风险升高"""
        avg_low_alpha = {"delta": 15, "theta": 10, "alpha": 3, "beta": 5, "gamma": 2}
        avg_normal_alpha = {"delta": 15, "theta": 10, "alpha": 30, "beta": 5, "gamma": 2}
        m_low = eeg.compute_health_metrics(avg_low_alpha)
        m_normal = eeg.compute_health_metrics(avg_normal_alpha)
        assert m_low["cerebrovascular_risk"] > m_normal["cerebrovascular_risk"]

    # ⭐ 赛道7新增：认知衰退风险测试
    def test_cognitive_decline_risk_high_theta(self):
        """θ波增多+α波减少时认知衰退风险升高"""
        avg_mci = {"delta": 10, "theta": 25, "alpha": 8, "beta": 5, "gamma": 2}
        avg_normal = {"delta": 10, "theta": 8, "alpha": 30, "beta": 10, "gamma": 5}
        m_mci = eeg.compute_health_metrics(avg_mci)
        m_normal = eeg.compute_health_metrics(avg_normal)
        assert m_mci["cognitive_decline_risk"] > m_normal["cognitive_decline_risk"]

    def test_cognitive_decline_risk_theta_alpha_ratio(self):
        """θ/α比值升高时认知衰退风险升高"""
        avg_high_ratio = {"delta": 10, "theta": 20, "alpha": 10, "beta": 5, "gamma": 2}
        avg_low_ratio = {"delta": 10, "theta": 5, "alpha": 30, "beta": 5, "gamma": 2}
        m_high = eeg.compute_health_metrics(avg_high_ratio)
        m_low = eeg.compute_health_metrics(avg_low_ratio)
        assert m_high["cognitive_decline_risk"] > m_low["cognitive_decline_risk"]

    # ⭐ 赛道7新增：精神状态筛查测试
    def test_mental_health_structure(self):
        """精神状态筛查结果结构完整"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        mh = m["mental_health"]
        assert "anxiety_score" in mh
        assert "depression_score" in mh
        assert "overall_risk" in mh
        assert "screening_label" in mh
        assert mh["screening_label"] in {"焦虑倾向", "抑郁倾向", "情绪风险", "正常"}

    def test_mental_health_anxiety_high_beta(self):
        """β波过度活跃时焦虑评分升高"""
        avg_high_beta = {"delta": 5, "theta": 5, "alpha": 5, "beta": 30, "gamma": 10}
        avg_normal = {"delta": 10, "theta": 8, "alpha": 30, "beta": 10, "gamma": 5}
        m_high = eeg.compute_health_metrics(avg_high_beta)
        m_normal = eeg.compute_health_metrics(avg_normal)
        assert m_high["mental_health"]["anxiety_score"] > m_normal["mental_health"]["anxiety_score"]

    def test_mental_health_overall_is_max(self):
        """overall_risk 是 anxiety 和 depression 的最大值"""
        avg = {"delta": 10, "theta": 8, "alpha": 15, "beta": 5, "gamma": 2}
        m = eeg.compute_health_metrics(avg)
        mh = m["mental_health"]
        assert abs(mh["overall_risk"] - max(mh["anxiety_score"], mh["depression_score"])) < 0.1


# ============================================================
# 5. 脑电异常预警
# ============================================================

class TestAlerts:
    """脑电异常预警规则测试"""

    def test_high_stress_alert(self):
        """高压力触发 high 级预警"""
        metrics = {
            "stress_index": 85, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 0.3, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics, {"name": "测试"})
        titles = [a["title"] for a in alerts]
        assert any("高压力" in t for t in titles)
        high_alerts = [a for a in alerts if a["level"] == "high"]
        assert len(high_alerts) >= 1

    def test_medium_stress_alert(self):
        """中等压力触发 medium 级预警"""
        metrics = {
            "stress_index": 55, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        stress_alerts = [a for a in alerts if "压力" in a["title"]]
        assert len(stress_alerts) == 1
        assert stress_alerts[0]["level"] == "medium"

    def test_poor_sleep_alert(self):
        """睡眠质量差触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 30, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.2, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        sleep_alerts = [a for a in alerts if "睡眠" in a["title"]]
        assert len(sleep_alerts) == 1
        assert sleep_alerts[0]["level"] == "high"

    def test_cognitive_overload_alert(self):
        """认知负荷过高触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 80,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.4},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        cog_alerts = [a for a in alerts if "认知" in a["title"]]
        assert len(cog_alerts) == 1

    def test_low_attention_alert(self):
        """注意力偏低触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 30,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 3.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        att_alerts = [a for a in alerts if "注意力" in a["title"]]
        assert len(att_alerts) == 1

    def test_emotion_abnormal_alert(self):
        """情绪异常触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "焦虑倾向", "valence": 30, "arousal": 70},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        emotion_alerts = [a for a in alerts if "情绪" in a["title"]]
        assert len(emotion_alerts) == 1

    def test_no_alerts_when_healthy(self):
        """健康指标不触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 70,
            "sleep_quality": 75, "cognitive_load": 50,
            "emotion": {"label": "平静放松", "valence": 65, "arousal": 35},
            "ratios": {"alpha_beta": 2.0, "theta_beta": 0.8, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        assert len(alerts) == 0

    def test_alerts_have_evidence(self):
        """预警带 evidence 字段（可解释性）"""
        metrics = {
            "stress_index": 85, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 0.3, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        for a in alerts:
            assert "evidence" in a
            assert len(a["evidence"]) > 0

    def test_alerts_sorted_by_severity(self):
        """预警按等级排序（high 优先）"""
        metrics = {
            "stress_index": 85, "attention_index": 30,
            "sleep_quality": 30, "cognitive_load": 80,
            "emotion": {"label": "焦虑倾向", "valence": 30, "arousal": 70},
            "ratios": {"alpha_beta": 0.3, "theta_beta": 3.0, "slow_wave_ratio": 0.2, "fast_wave_ratio": 0.4},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        if len(alerts) > 1:
            order = {"high": 0, "medium": 1, "low": 2}
            for i in range(len(alerts) - 1):
                assert order[alerts[i]["level"]] <= order[alerts[i + 1]["level"]]

    # ⭐ 赛道7新增：脑血管风险预警测试
    def test_cerebrovascular_high_alert(self):
        """脑血管风险≥60触发high级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 1.5, "delta_ratio": 0.4},
            "cerebrovascular_risk": 75,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 20, "depression_score": 15, "overall_risk": 20, "screening_label": "正常"},
        }
        alerts = eeg.scan_eeg_alerts(metrics, {"name": "测试", "age": 65})
        cv_alerts = [a for a in alerts if "脑血管" in a["title"]]
        assert len(cv_alerts) == 1
        assert cv_alerts[0]["level"] == "high"
        assert cv_alerts[0]["category"] == "cerebrovascular"
        assert "evidence" in cv_alerts[0]

    def test_cerebrovascular_medium_alert(self):
        """脑血管风险40-59触发medium级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 1.0, "delta_ratio": 0.3},
            "cerebrovascular_risk": 50,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 20, "depression_score": 15, "overall_risk": 20, "screening_label": "正常"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        cv_alerts = [a for a in alerts if "脑血管" in a["title"]]
        assert len(cv_alerts) == 1
        assert cv_alerts[0]["level"] == "medium"

    def test_cerebrovascular_no_alert_when_low(self):
        """脑血管风险<40不触发预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 0.6, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 20, "depression_score": 15, "overall_risk": 20, "screening_label": "正常"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        cv_alerts = [a for a in alerts if "脑血管" in a["title"]]
        assert len(cv_alerts) == 0

    # ⭐ 赛道7新增：认知衰退风险预警测试
    def test_cognitive_decline_high_alert(self):
        """认知衰退风险≥60触发high级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 1.5, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 70,
            "mental_health": {"anxiety_score": 20, "depression_score": 15, "overall_risk": 20, "screening_label": "正常"},
        }
        alerts = eeg.scan_eeg_alerts(metrics, {"name": "测试", "age": 70})
        cd_alerts = [a for a in alerts if "认知" in a["title"] and "衰退" in a["title"]]
        assert len(cd_alerts) == 1
        assert cd_alerts[0]["level"] == "high"
        assert cd_alerts[0]["category"] == "cognitive_decline"

    def test_cognitive_decline_medium_alert(self):
        """认知衰退风险40-59触发medium级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 1.0, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 50,
            "mental_health": {"anxiety_score": 20, "depression_score": 15, "overall_risk": 20, "screening_label": "正常"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        cd_alerts = [a for a in alerts if "认知" in a["title"] and ("衰退" in a["title"] or "下降" in a["title"])]
        assert len(cd_alerts) == 1
        assert cd_alerts[0]["level"] == "medium"

    # ⭐ 赛道7新增：精神状态预警测试
    def test_mental_health_high_alert_anxiety(self):
        """焦虑倾向≥60触发high级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 0.6, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 75, "depression_score": 40, "overall_risk": 75, "screening_label": "焦虑倾向"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        mh_alerts = [a for a in alerts if "精神状态" in a["title"] or "焦虑" in a["title"]]
        assert len(mh_alerts) == 1
        assert mh_alerts[0]["level"] == "high"
        assert mh_alerts[0]["category"] == "mental_health"

    def test_mental_health_high_alert_depression(self):
        """抑郁倾向≥60触发high级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 0.6, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 40, "depression_score": 70, "overall_risk": 70, "screening_label": "抑郁倾向"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        mh_alerts = [a for a in alerts if "精神状态" in a["title"] or "抑郁" in a["title"]]
        assert len(mh_alerts) == 1
        assert mh_alerts[0]["level"] == "high"

    def test_mental_health_medium_alert(self):
        """情绪风险40-59触发medium级预警"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "emotion": {"label": "情绪平稳", "valence": 50, "arousal": 50},
            "ratios": {"alpha_beta": 1.0, "theta_beta": 1.0, "slow_wave_ratio": 0.4, "fast_wave_ratio": 0.2, "theta_alpha": 0.6, "delta_ratio": 0.2},
            "cerebrovascular_risk": 25,
            "cognitive_decline_risk": 20,
            "mental_health": {"anxiety_score": 45, "depression_score": 40, "overall_risk": 45, "screening_label": "情绪风险"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        mh_alerts = [a for a in alerts if "情绪状态" in a["title"] or "精神" in a["title"]]
        assert len(mh_alerts) == 1
        assert mh_alerts[0]["level"] == "medium"

    def test_alerts_have_category(self):
        """所有预警都有category字段"""
        metrics = {
            "stress_index": 85, "attention_index": 30,
            "sleep_quality": 30, "cognitive_load": 80,
            "emotion": {"label": "焦虑倾向", "valence": 30, "arousal": 70},
            "ratios": {"alpha_beta": 0.3, "theta_beta": 3.0, "slow_wave_ratio": 0.2, "fast_wave_ratio": 0.4, "theta_alpha": 1.5, "delta_ratio": 0.4},
            "cerebrovascular_risk": 70,
            "cognitive_decline_risk": 65,
            "mental_health": {"anxiety_score": 70, "depression_score": 40, "overall_risk": 70, "screening_label": "焦虑倾向"},
        }
        alerts = eeg.scan_eeg_alerts(metrics)
        for a in alerts:
            assert "category" in a
            assert a["category"] in {"stress", "sleep", "cognitive", "attention", "emotion", "cerebrovascular", "cognitive_decline", "mental_health"}


# ============================================================
# 6. 医保政策联动
# ============================================================

class TestPolicyLink:
    """脑电异常 → 医保政策联动测试"""

    def test_load_policy_link(self):
        """加载政策联动规则库"""
        rules = eeg.load_eeg_policy_link()
        assert "links" in rules
        assert len(rules["links"]) > 0

    def test_high_stress_links_policy(self):
        """高压力触发心理科政策联动"""
        metrics = {
            "stress_index": 85, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
        }
        links = eeg.link_to_policies(metrics)
        triggers = [l["trigger"] for l in links]
        assert "high_stress" in triggers
        for l in links:
            assert "policy_hint" in l
            assert "related_policies" in l

    def test_poor_sleep_links_policy(self):
        """睡眠差触发睡眠监测政策联动"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 30, "cognitive_load": 50,
        }
        links = eeg.link_to_policies(metrics)
        triggers = [l["trigger"] for l in links]
        assert "poor_sleep" in triggers

    def test_no_links_when_healthy(self):
        """健康指标不触发政策联动"""
        metrics = {
            "stress_index": 30, "attention_index": 70,
            "sleep_quality": 75, "cognitive_load": 50,
        }
        links = eeg.link_to_policies(metrics)
        assert len(links) == 0

    def test_links_have_evidence(self):
        """联动推荐带 evidence"""
        metrics = {"stress_index": 85, "attention_index": 60, "sleep_quality": 30, "cognitive_load": 80}
        links = eeg.link_to_policies(metrics, {"name": "测试用户"})
        for l in links:
            assert "evidence" in l
            assert len(l["evidence"]) > 0

    # ⭐ 赛道7新增：脑血管风险政策联动测试
    def test_cerebrovascular_links_policy(self):
        """脑血管风险触发脑血管病政策联动"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "cerebrovascular_risk": 75,
        }
        links = eeg.link_to_policies(metrics)
        triggers = [l["trigger"] for l in links]
        assert "cerebrovascular_risk" in triggers
        cv_link = [l for l in links if l["trigger"] == "cerebrovascular_risk"][0]
        assert "脑血管" in cv_link["policy_hint"] or "脑血管" in cv_link["title"]
        assert len(cv_link["related_policies"]) >= 3

    # ⭐ 赛道7新增：认知衰退政策联动测试
    def test_cognitive_decline_links_policy(self):
        """认知衰退风险触发认知评估政策联动"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "cognitive_decline_risk": 70,
        }
        links = eeg.link_to_policies(metrics)
        triggers = [l["trigger"] for l in links]
        assert "cognitive_decline_risk" in triggers
        cd_link = [l for l in links if l["trigger"] == "cognitive_decline_risk"][0]
        assert "认知" in cd_link["title"] or "认知" in cd_link["policy_hint"]

    # ⭐ 赛道7新增：精神状态政策联动测试
    def test_mental_health_links_policy(self):
        """精神状态风险触发精神卫生政策联动"""
        metrics = {
            "stress_index": 30, "attention_index": 60,
            "sleep_quality": 70, "cognitive_load": 50,
            "mental_health": {"overall_risk": 75, "anxiety_score": 75, "depression_score": 40, "screening_label": "焦虑倾向"},
        }
        links = eeg.link_to_policies(metrics)
        triggers = [l["trigger"] for l in links]
        assert "mental_health_risk" in triggers
        mh_link = [l for l in links if l["trigger"] == "mental_health_risk"][0]
        assert "精神" in mh_link["title"] or "心理" in mh_link["policy_hint"]

    def test_policy_link_file_has_new_triggers(self):
        """政策联动规则库文件包含赛道7新增触发器"""
        rules = eeg.load_eeg_policy_link()
        triggers = [l["trigger"] for l in rules["links"]]
        assert "cerebrovascular_risk" in triggers
        assert "cognitive_decline_risk" in triggers
        assert "mental_health_risk" in triggers


# ============================================================
# 7. 完整会话评估
# ============================================================

class TestAssessSession:
    """完整 EEG 会话评估测试"""

    def test_session_structure(self):
        """会话结果结构完整"""
        session = eeg.assess_session(user_id="1", mental_state="relaxed", seed=42)
        assert session.session_id.startswith("eeg_1_")
        assert session.user_id == "1"
        assert session.mental_state == "relaxed"
        assert session.mental_state_label == "放松"
        assert session.channels == eeg.CHANNELS
        assert session.sample_rate == eeg.SAMPLE_RATE
        assert len(session.waveform) == 4

    def test_session_to_dict(self):
        """to_dict 序列化完整"""
        session = eeg.assess_session(user_id="1", seed=42)
        d = session.to_dict()
        for key in ("session_id", "user_id", "timestamp", "duration_seconds",
                     "channels", "sample_rate", "mental_state", "mental_state_label",
                     "band_powers", "avg_band_powers", "metrics", "alerts",
                     "policy_links", "summary", "waveform"):
            assert key in d

    def test_session_band_powers_structure(self):
        """频段功率结构正确"""
        session = eeg.assess_session(user_id="1", seed=42)
        assert set(session.band_powers.keys()) == set(eeg.CHANNELS)
        for ch in eeg.CHANNELS:
            assert set(session.band_powers[ch].keys()) == set(eeg.BANDS.keys())
        assert set(session.avg_band_powers.keys()) == set(eeg.BANDS.keys())

    def test_session_metrics_valid(self):
        """健康指标有效"""
        session = eeg.assess_session(user_id="1", seed=42)
        m = session.metrics
        for key in ("stress_index", "attention_index", "sleep_quality", "cognitive_load"):
            assert 0 <= m[key] <= 100

    def test_session_waveform_downsampled(self):
        """波形降采样到 128 点"""
        session = eeg.assess_session(user_id="1", seed=42)
        for ch_wave in session.waveform:
            assert len(ch_wave["data"]) <= 128
            for point in ch_wave["data"]:
                assert "i" in point and "v" in point

    def test_session_summary_contains_metrics(self):
        """汇总包含关键指标"""
        session = eeg.assess_session(user_id="1", mental_state="stressed", seed=42)
        assert "压力指数" in session.summary
        assert "注意力" in session.summary
        assert "睡眠质量" in session.summary

    def test_stressed_session_has_alerts(self):
        """高压力状态会话产生预警"""
        session = eeg.assess_session(user_id="1", mental_state="stressed", seed=42)
        assert len(session.alerts) > 0

    def test_stressed_session_has_policy_links(self):
        """高压力状态会话触发政策联动"""
        session = eeg.assess_session(user_id="1", mental_state="stressed", seed=42)
        assert len(session.policy_links) > 0

    def test_invalid_state_fallback(self):
        """无效状态回退到 relaxed"""
        session = eeg.assess_session(user_id="1", mental_state="invalid", seed=42)
        assert session.mental_state == "relaxed"

    def test_session_reproducible(self):
        """相同种子可复现"""
        s1 = eeg.assess_session(user_id="1", seed=99)
        s2 = eeg.assess_session(user_id="1", seed=99)
        assert s1.avg_band_powers == s2.avg_band_powers
        assert s1.metrics == s2.metrics


# ============================================================
# 8. 实时数据块
# ============================================================

class TestRealtimeStream:
    """实时数据块生成测试"""

    def test_realtime_structure(self):
        """实时块结构完整"""
        chunk = eeg.realtime_stream(mental_state="relaxed", seed=42)
        assert "channel" in chunk
        assert "waveform" in chunk
        assert "band_powers" in chunk
        assert "metrics_snapshot" in chunk
        assert "timestamp" in chunk

    def test_realtime_waveform_downsampled(self):
        """实时波形降采样到 64 点"""
        chunk = eeg.realtime_stream(seed=42)
        assert len(chunk["waveform"]) <= 64
        for point in chunk["waveform"]:
            assert "i" in point and "v" in point

    def test_realtime_metrics_snapshot(self):
        """实时指标快照包含压力和注意力"""
        chunk = eeg.realtime_stream(seed=42)
        snap = chunk["metrics_snapshot"]
        assert "stress_index" in snap
        assert "attention_index" in snap
        assert 0 <= snap["stress_index"] <= 100


# ============================================================
# 9. 用户画像推荐心理状态
# ============================================================

class TestPickMentalState:
    """用户画像推荐心理状态测试"""

    def test_no_profile_returns_relaxed_or_focused(self):
        """无画像返回放松或专注"""
        state = eeg.pick_mental_state_by_profile(None)
        assert state in ("relaxed", "focused")

    def test_not_found_returns_relaxed_or_focused(self):
        """画像未找到返回放松或专注"""
        state = eeg.pick_mental_state_by_profile({"found": False})
        assert state in ("relaxed", "focused")

    def test_elderly_returns_risk_state(self):
        """高龄用户返回风险状态"""
        profile = {"found": True, "age": 70, "chronic_diseases": []}
        state = eeg.pick_mental_state_by_profile(profile)
        assert state in ("fatigued", "sleep_deprived", "stressed")

    def test_multi_chronic_returns_risk_state(self):
        """多种慢病返回风险状态"""
        profile = {"found": True, "age": 50, "chronic_diseases": ["糖尿病", "高血压"]}
        state = eeg.pick_mental_state_by_profile(profile)
        assert state in ("fatigued", "sleep_deprived", "stressed")

    def test_single_chronic_returns_stressed_or_fatigued(self):
        """单种慢病返回压力或疲劳"""
        profile = {"found": True, "age": 40, "chronic_diseases": ["高血压"]}
        state = eeg.pick_mental_state_by_profile(profile)
        assert state in ("stressed", "fatigued")


# ============================================================
# 10. 状态差异验证（BCI×医保创新核心）
# ============================================================

class TestStateDifferentiation:
    """不同心理状态指标差异验证"""

    def test_stressed_vs_relaxed_stress_diff(self):
        """高压力状态压力指数高于放松状态"""
        relaxed = eeg.assess_session(user_id="1", mental_state="relaxed", seed=42)
        stressed = eeg.assess_session(user_id="1", mental_state="stressed", seed=42)
        assert stressed.metrics["stress_index"] > relaxed.metrics["stress_index"]

    def test_focused_vs_fatigued_attention_diff(self):
        """专注状态注意力高于疲劳状态"""
        focused = eeg.assess_session(user_id="1", mental_state="focused", seed=42)
        fatigued = eeg.assess_session(user_id="1", mental_state="fatigued", seed=42)
        assert focused.metrics["attention_index"] > fatigued.metrics["attention_index"]

    def test_sleep_quality_varies_by_state(self):
        """不同心理状态睡眠质量存在差异（状态可区分）"""
        results = {}
        for state in eeg.MENTAL_STATES:
            s = eeg.assess_session(user_id="1", mental_state=state, seed=42)
            results[state] = s.metrics["sleep_quality"]
        # 不同状态应产生不同的睡眠质量值（状态可区分）
        unique_values = set(results.values())
        assert len(unique_values) >= 3
