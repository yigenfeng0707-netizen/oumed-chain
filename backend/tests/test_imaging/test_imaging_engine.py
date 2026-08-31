"""医学影像 AI 标注引擎单元测试（瓯医数链 Agent 影像引擎 v3.0）

覆盖：
- 常量与配置（检查类型 / 病灶类别元数据）
- 合成影像渲染（确定性、尺寸、灰度范围）
- AI 病灶检测流水线（预处理、局部对比度、连通域、分类打分）
- 完整会话生成（影像 + AI 预标注 + 结构化报告）
- 医生复核标注（确认 / 驳回 / 新增）
- 影像异常 → 医保检查联动
- 健康检查集成
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pytest

from app.services.imaging import engine as img

# ============================================================
# 1. 常量与配置
# ============================================================

class TestConstants:
    """引擎常量配置测试"""

    def test_study_types_three(self):
        """支持三种检查类型"""
        assert set(img.STUDY_TYPES.keys()) == {"chest_xray", "lung_ct", "brain_mri"}

    def test_study_types_have_findings(self):
        """每种检查类型均配置病灶类别"""
        for key, meta in img.STUDY_TYPES.items():
            assert meta["label"]
            assert len(meta["findings"]) >= 3

    def test_findings_meta_complete(self):
        """所有病灶类别均有完整元数据"""
        for key, meta in img.FINDINGS_META.items():
            assert meta["label"]
            assert meta["severity"] in ("low", "medium", "high")
            assert meta["tone"] in ("light", "dark")
            assert meta["desc"]

    def test_image_size(self):
        """合成影像为 512×512"""
        assert img.IMG_SIZE == 512


# ============================================================
# 2. 合成影像渲染
# ============================================================

class TestSynthesis:
    """合成医学影像渲染测试"""

    @pytest.mark.parametrize("study_type", ["chest_xray", "lung_ct", "brain_mri"])
    def test_render_deterministic(self, study_type):
        """相同种子渲染结果可复现（确定性）"""
        findings = [img.Finding(
            finding_type=img.STUDY_TYPES[study_type]["findings"][0],
            x=0.5, y=0.5, w=0.08, h=0.08,
        )]
        a = img.render_study_image(study_type, findings, seed=42)
        b = img.render_study_image(study_type, findings, seed=42)
        assert a == b

    @pytest.mark.parametrize("study_type", ["chest_xray", "lung_ct", "brain_mri"])
    def test_render_returns_base64_png(self, study_type):
        """渲染结果为 base64 PNG data URI"""
        findings = [img.Finding(
            finding_type=img.STUDY_TYPES[study_type]["findings"][0],
            x=0.5, y=0.5, w=0.08, h=0.08,
        )]
        uri = img.render_study_image(study_type, findings, seed=7)
        assert uri.startswith("data:image/png;base64,")
        assert len(uri) > 1000

    def test_render_different_seed_differs(self):
        """不同种子渲染解剖噪声不同（影像不雷同）"""
        findings = [img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.08, h=0.08)]
        a = img.render_study_image("chest_xray", findings, seed=1)
        b = img.render_study_image("chest_xray", findings, seed=2)
        assert a != b

    def test_render_without_pil_fallback(self, monkeypatch):
        """无 Pillow 时降级渲染不报错"""
        monkeypatch.setattr(img, "HAS_PIL", False)
        findings = [img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.08, h=0.08)]
        uri = img.render_study_image("chest_xray", findings, seed=3)
        assert uri.startswith("data:image/png;base64,")


# ============================================================
# 3. AI 病灶检测流水线
# ============================================================

class TestDetection:
    """病灶检测流水线测试"""

    def test_preprocess_normalizes(self):
        """预处理输出在 [0,1] 且保持形状"""
        arr = np.random.default_rng(0).integers(0, 255, (64, 64)).astype(float)
        out = img._preprocess(arr)
        assert out.shape == (64, 64)
        assert out.min() >= 0 and out.max() <= 1.001

    def test_local_contrast_enhance_detects_blob(self):
        """局部对比度增强能凸显亮斑（积分图实现正确性）"""
        arr = np.full((128, 128), 0.5)
        arr[50:60, 60:70] = 1.0  # 亮斑
        contrast = img._local_contrast_enhance(arr, radius=32)
        assert contrast[55, 65] > 0.3  # 亮斑中心高对比
        assert abs(contrast[10, 10]) < 0.1  # 均匀区近零

    def test_connected_components_finds_region(self):
        """连通域分析能分离两块独立区域"""
        mask = np.zeros((100, 100), dtype=bool)
        mask[10:20, 10:20] = True
        mask[60:70, 60:70] = True
        regions = img._connected_components(mask, min_size=5)
        assert len(regions) == 2

    def test_connected_components_small_filtered(self):
        """过小区域被过滤"""
        mask = np.zeros((50, 50), dtype=bool)
        mask[10:11, 10:11] = True  # 1px
        assert img._connected_components(mask, min_size=5) == []

    def test_region_features_extraction(self):
        """区域特征提取正确"""
        arr = np.zeros((100, 100))
        arr[10:30, 10:30] = 0.8
        region_y, region_x = np.mgrid[10:30, 10:30]
        feats = img._region_features(arr, region_y.ravel(), region_x.ravel())
        assert feats["area"] == 400
        assert feats["fill_ratio"] == pytest.approx(1.0)
        assert feats["cy"] == pytest.approx(19.5)
        assert feats["mean_intensity"] == pytest.approx(0.8)

    def test_detect_findings_returns_boxes(self):
        """完整检测流水线输出 bbox + 类别 + 置信度"""
        arr = np.full((256, 256), 128.0)
        # 植入两个亮斑（结节样）
        for cx, cy in [(100, 100), (160, 140)]:
            yy, xx = np.mgrid[0:256, 0:256]
            arr[((xx - cx) / 12) ** 2 + ((yy - cy) / 12) ** 2 < 1] = 240
        findings = img.detect_findings(arr, "chest_xray")
        assert len(findings) >= 1
        for f in findings:
            assert 0 <= f.x <= 1 and 0 <= f.y <= 1
            assert 0 < f.w <= 1 and 0 < f.h <= 1
            assert 0 <= f.confidence <= 1
            assert f.finding_type in img.FINDINGS_META

    def test_detect_dedup(self):
        """重叠区域去重后数量受控"""
        findings = [
            img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.1, h=0.1, confidence=0.9),
            img.Finding(finding_type="nodule", x=0.51, y=0.5, w=0.1, h=0.1, confidence=0.8),
        ]
        deduped = []
        for f in findings:
            if all(img._iou(f, g) < 0.5 for g in deduped):
                deduped.append(f)
        assert len(deduped) == 1

    def test_iou_identical(self):
        """相同 bbox IoU = 1"""
        a = img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.1, h=0.1)
        b = img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.1, h=0.1)
        assert img._iou(a, b) == pytest.approx(1.0)


# ============================================================
# 4. 完整会话生成
# ============================================================

class TestStudyGeneration:
    """影像会话生成测试"""

    @pytest.mark.parametrize("study_type", ["chest_xray", "lung_ct", "brain_mri"])
    def test_generate_study_basic(self, study_type):
        """三种检查类型均可生成完整会话"""
        study = img.generate_study(study_type=study_type, seed=10)
        assert study.study_id.startswith("ST")
        assert study.study_type == study_type
        assert study.image_base64.startswith("data:image/png;base64,")
        assert len(study.findings) >= 1
        assert study.report["conclusion"]
        assert study.report["disclaimer"]

    def test_generate_study_seed_deterministic(self):
        """相同种子生成相同发现集（可复现）"""
        a = img.generate_study("chest_xray", seed=99)
        b = img.generate_study("chest_xray", seed=99)
        assert [f.to_dict() for f in a.findings] == [f.to_dict() for f in b.findings]
        assert a.image_base64 == b.image_base64

    def test_generate_study_default_findings(self):
        """缺省病灶类别使用该类型全部类别"""
        study = img.generate_study("chest_xray", seed=5)
        keys = {f.finding_type for f in study.findings}
        assert len(keys) >= 1

    def test_generate_study_specific_findings(self):
        """指定病灶类别可生成对应影像"""
        study = img.generate_study("lung_ct", findings_keys=["nodule", "ground_glass"], seed=5)
        keys = {f.finding_type for f in study.findings}
        assert "nodule" in keys or "ground_glass" in keys

    def test_generate_study_invalid_type(self):
        """非法检查类型报错"""
        with pytest.raises(ValueError):
            img.generate_study("invalid_type", seed=1)

    def test_finding_to_dict_shape(self):
        """Finding 序列化结构完整"""
        f = img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.08, h=0.08,
                        confidence=0.88, severity="medium")
        d = f.to_dict()
        assert d["finding_type"] == "nodule"
        assert d["label"] == "肺结节"
        assert d["confidence"] == 0.88
        assert d["severity"] == "medium"
        assert d["status"] == "pending"
        assert d["source"] == "ai"


# ============================================================
# 5. 医生复核标注
# ============================================================

class TestDoctorReview:
    """医生复核流程测试"""

    def _sample_findings(self):
        return [
            img.Finding(finding_type="nodule", x=0.4, y=0.4, w=0.06, h=0.06,
                        confidence=0.85, severity="medium", source="ai", status="pending"),
            img.Finding(finding_type="effusion", x=0.7, y=0.8, w=0.2, h=0.1,
                        confidence=0.78, severity="high", source="ai", status="pending"),
        ]

    def test_confirm(self):
        """医生确认 AI 标注"""
        result = img.apply_doctor_review(self._sample_findings(), [
            {"action": "confirm", "index": 0},
        ])
        assert result[0].status == "confirmed"
        assert result[1].status == "pending"

    def test_reject(self):
        """医生驳回 AI 标注（误检）"""
        result = img.apply_doctor_review(self._sample_findings(), [
            {"action": "reject", "index": 1},
        ])
        assert result[1].status == "rejected"

    def test_add_doctor_finding(self):
        """医生补充标注（AI 漏检）"""
        result = img.apply_doctor_review(self._sample_findings(), [
            {"action": "add", "finding_type": "cardiomegaly",
             "x": 0.35, "y": 0.6, "w": 0.1, "h": 0.12,
             "confidence": 0.95, "severity": "medium"},
        ])
        assert len(result) == 3
        assert result[-1].source == "doctor"
        assert result[-1].status == "confirmed"
        assert result[-1].finding_type == "cardiomegaly"

    def test_report_with_confirmed(self):
        """报告基于最终标注生成结论"""
        result = self._sample_findings()
        for f in result:
            f.status = "confirmed"
        report = img.build_report(result)
        assert report["risk_level"] == "高"  # 存在 effusion(high)
        assert report["confirmed_count"] == 2
        assert "医师复核" in report["disclaimer"]

    def test_report_empty(self):
        """无标注时报告提示待复核"""
        report = img.build_report([])
        assert report["risk_level"] == "待复核"


# ============================================================
# 6. 影像-医保联动
# ============================================================

class TestPolicyLink:
    """影像异常 → 医保检查联动测试"""

    def test_high_risk_trigger(self):
        """高危异常触发医保联动"""
        findings = [img.Finding(finding_type="tumor", x=0.5, y=0.5, w=0.1, h=0.1, severity="high")]
        links = img.link_to_imaging_policies(findings)
        assert any(l["trigger"] == "finding_high_risk" for l in links)
        assert any(l["trigger"] == "brain_acute" for l in links)

    def test_nodule_followup(self):
        """结节触发随访政策"""
        findings = [img.Finding(finding_type="nodule", x=0.5, y=0.5, w=0.06, h=0.06, severity="medium")]
        links = img.link_to_imaging_policies(findings)
        assert any(l["trigger"] == "nodule_followup" for l in links)

    def test_normal_no_link(self):
        """无明显异常时无政策联动"""
        findings = []
        assert img.link_to_imaging_policies(findings) == []

    def test_policy_link_file_loaded(self):
        """联动规则库可加载（含文件版）"""
        rules = img.load_imaging_policy_link()
        assert len(rules["links"]) >= 4
        for link in rules["links"]:
            assert link["trigger"]
            assert link["title"]
            assert link["suggestion"]
