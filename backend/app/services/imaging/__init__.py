"""瓯医数链 Agent - 医学影像 AI 标注引擎（Imaging Engine）

多模态医疗信号识别核心模块，与 EEG 脑电引擎并列，构成"脑电 + 影像"
双模态关键医疗信号识别闭环。

v3.0 新增：医学影像 AI 标注（合成影像生成、病灶检测、AI 预标注、
医生复核、结构化报告、医保检查联动）。
"""

from app.services.imaging.engine import (
    FINDINGS_META,
    STUDY_TYPES,
    Finding,
    ImagingStudy,
    apply_doctor_review,
    build_report,
    detect_findings,
    generate_study,
    link_to_imaging_policies,
    load_imaging_policy_link,
    render_study_image,
)

__all__ = [
    "FINDINGS_META",
    "STUDY_TYPES",
    "Finding",
    "ImagingStudy",
    "apply_doctor_review",
    "build_report",
    "detect_findings",
    "generate_study",
    "link_to_imaging_policies",
    "load_imaging_policy_link",
    "render_study_image",
]
