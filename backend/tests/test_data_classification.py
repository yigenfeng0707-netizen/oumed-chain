"""P0-1.3 数据分类分级台账测试（app/data_classification.py）：

验收标准："平台内字段级分级标签覆盖 100% 数据表"——
- 遍历 SQLAlchemy 元数据全部表/列，每列必须在台账登记（新增列未登记 → 测试红）；
- 台账反向校验：登记的表/列必须真实存在（防改名后台账悬空）；
- 敏感字段级别抽查（email/诊断/用药/脑电/影像/泛癌 = 4 级）；
- 待加密清单：已落地加密的字段不出现在 pending 清单。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import data_classification as dc  # noqa: E402
from app.models import Base  # noqa: E402


def _model_columns() -> dict[str, set[str]]:
    """从 SQLAlchemy 元数据取全部表的真实列清单。"""
    return {table.name: {col.name for col in table.columns}
            for table in Base.metadata.tables.values()}


def test_registry_covers_100_percent_of_tables_and_columns():
    """验收硬校验：模型全部表/列 100% 有分级标签。"""
    model_cols = _model_columns()
    registry = dc.registry()
    missing = []
    for table, columns in model_cols.items():
        assert table in registry, f"表 {table} 未登记分级台账"
        for col in columns:
            if col not in registry[table]:
                missing.append(f"{table}.{col}")
    assert not missing, f"以下字段缺分级标签：{missing}"


def test_registry_has_no_ghost_entries():
    """台账反向校验：登记条目必须对应真实表/列（防改名悬空）。"""
    model_cols = _model_columns()
    ghosts = []
    for table, fields in dc.registry().items():
        assert table in model_cols, f"台账中的表不存在于模型：{table}"
        for field in fields:
            if field not in model_cols[table]:
                ghosts.append(f"{table}.{field}")
    assert not ghosts, f"台账悬空条目（模型中已不存在）：{ghosts}"


def test_sensitive_fields_are_level_4():
    """敏感健康医疗数据抽查：核心字段必须 4 级。"""
    expect_level_4 = [
        ("medical_records", "diagnosis"),
        ("medication_records", "medication_name"),
        ("medication_records", "is_chronic"),
        ("chat_messages", "content"),
        ("eeg_records", "metrics"),
        ("imaging_records", "report"),
        ("cancer_prediction_records", "result"),
        ("body_records", "description"),
        ("body_records", "raw_excerpt"),
        ("body_archive_files", "content"),
        ("body_documents", "extracted_text"),
    ]
    for table, field in expect_level_4:
        assert dc.get_field_level(table, field) == 4, f"{table}.{field} 应为 4 级"


def test_identity_fields_are_level_3():
    """一般个人信息抽查：身份标识/财产类 3 级。"""
    assert dc.get_field_level("users", "email") == 3
    assert dc.get_field_level("users", "name") == 3
    assert dc.get_field_level("insurance_records", "base_amount") == 3
    assert dc.get_field_level("access_denial_logs", "client_ip") == 3


def test_pending_encryption_list_consistent():
    """已落地加密（encrypted=True）的字段不出现在待加密清单。"""
    pending = {(t, f) for t, fs in dc.pending_encryption_fields().items() for f in fs}
    for table, fields in dc.registry().items():
        for field, meta in fields.items():
            if meta.get("encrypted"):
                assert (table, field) not in pending
            if meta["level"] == 4 and not meta.get("encrypted"):
                assert (table, field) in pending


def test_levels_and_categories_documented():
    """级别/类别字典完整（5 级模型 + 6 大类）。"""
    assert set(dc.LEVELS) == {1, 2, 3, 4, 5}
    assert set(dc.CATEGORIES) == {"个人属性", "健康状况", "医疗应用", "卫生资源", "公共卫生", "其他"}


def test_fields_by_level_reverse_lookup():
    """按级别反查可用（治理专项导出）。"""
    level4 = dc.fields_by_level(4)
    assert "medical_records" in level4 and "diagnosis" in level4["medical_records"]
