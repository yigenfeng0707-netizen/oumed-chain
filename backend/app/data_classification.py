"""数据分类分级字段级台账（P0-1.3 数据分类分级管理办法的机器可读载体）。

双框架口径：
- 分级遵循 GB/T 39725-2020《健康医疗数据安全指南》5 级模型（1 公开 / 2 内部 /
  3 一般个人信息 / 4 敏感健康医疗数据 / 5 特殊敏感数据）；
- 类别遵循该指南 6 大类：个人属性 / 健康状况 / 医疗应用 / 卫生资源 / 公共卫生 / 其他；
- 对照 GB/T 43697-2024《数据分类分级规则》：4 级字段批量汇集 = 重要数据，
  单条流通需去标识化；本平台无核心数据（目录级映射见 docs/数据分类分级管理办法.md）。

本台账是"字段级分级标签覆盖 100% 数据表"验收的唯一事实源：
tests/test_data_classification.py 遍历 SQLAlchemy 元数据强制校验
（新增表/列而未登记 → 测试红），覆盖 23 张表全字段。

配套策略（按级别，详见管理办法）：
- 4 级：存储加密 + 单独同意 + 禁出域交易 + 导出脱敏（encrypted=True 标记已落地加密）；
- 3 级：访问控制 + 传输加密 + 展示脱敏（邮箱/IP 等身份标识）；
- 2 级：访问控制 + 会话校验；
- 1 级：常规保护（备份/审计兜底）。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 级别与类别字典（GB/T 39725-2020）
# ---------------------------------------------------------------------------

LEVELS: dict[int, str] = {
    1: "公开/非个人（常规保护）",
    2: "内部（一般属性，访问控制）",
    3: "一般个人信息（身份标识/财产，访问控制+传输加密+展示脱敏）",
    4: "敏感健康医疗数据（存储加密+单独同意+禁出域交易+导出脱敏）",
    5: "特殊敏感数据（基因/生物样本等，本平台暂不涉及）",
}

CATEGORIES: dict[str, str] = {
    "个人属性": "身份标识与人口学属性",
    "健康状况": "诊断、用药、生理/心理评估、健康自述",
    "医疗应用": "就医记录、医疗服务消费与结算",
    "卫生资源": "医院/科室/政策/医保缴费资源",
    "公共卫生": "聚合统计与联邦聚合结果",
    "其他": "系统/审计/存证元数据",
}

# ---------------------------------------------------------------------------
# 字段级台账：表 → 字段 → {"category", "level", "note"(可选), "encrypted"(已落地加密)}
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, dict[str, dict]] = {
    "users": {
        "id": {"category": "其他", "level": 1, "note": "内部主键"},
        "name": {"category": "个人属性", "level": 3},
        "age": {"category": "个人属性", "level": 2},
        "gender": {"category": "个人属性", "level": 2},
        "city": {"category": "个人属性", "level": 2},
        "insurance_type": {"category": "卫生资源", "level": 2, "note": "参保类型"},
        "employee_status": {"category": "个人属性", "level": 2},
        "email": {"category": "个人属性", "level": 3, "encrypted": True,
                  "note": "确定性 AES-GCM（等值查询保序）；展示需打码 a***@x.cn"},
        "password_hash": {"category": "其他", "level": 3, "note": "PBKDF2 单向哈希，非明文可逆"},
        "role": {"category": "其他", "level": 1, "note": "RBAC 角色"},
        "created_at": {"category": "其他", "level": 1},
    },
    "chat_conversations": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1, "note": "内部外键"},
        "title": {"category": "健康状况", "level": 4,
                  "note": "对话标题由用户输入派生，可能含健康自述"},
        "created_at": {"category": "其他", "level": 1},
        "updated_at": {"category": "其他", "level": 1},
    },
    "chat_messages": {
        "id": {"category": "其他", "level": 1},
        "conversation_id": {"category": "其他", "level": 1},
        "role": {"category": "其他", "level": 1, "note": "user/assistant 标记"},
        "content": {"category": "健康状况", "level": 4,
                    "note": "用户输入健康自述；LLM 出域需最小化（PIA 4.3 出域面管控）"},
        "agent_type": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "insurance_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "year": {"category": "其他", "level": 1},
        "month": {"category": "其他", "level": 1},
        "base_amount": {"category": "卫生资源", "level": 3, "note": "缴费基数（财产类）"},
        "personal_amount": {"category": "卫生资源", "level": 3},
        "company_amount": {"category": "卫生资源", "level": 3},
    },
    "medical_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "date": {"category": "医疗应用", "level": 2, "note": "就诊日期"},
        "hospital": {"category": "卫生资源", "level": 2},
        "department": {"category": "卫生资源", "level": 2},
        "diagnosis": {"category": "健康状况", "level": 4, "encrypted": True,
                      "note": "诊断（敏感个人信息）；AES-GCM 已落地"},
        "visit_type": {"category": "医疗应用", "level": 2},
        "total_cost": {"category": "医疗应用", "level": 3, "note": "费用（财产类）"},
        "reimbursed_amount": {"category": "医疗应用", "level": 3},
    },
    "medication_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "date": {"category": "医疗应用", "level": 2},
        "medication_name": {"category": "健康状况", "level": 4, "encrypted": True,
                            "note": "药品名（用药史）；AES-GCM 已落地"},
        "category": {"category": "健康状况", "level": 2, "note": "药品大类"},
        "quantity": {"category": "医疗应用", "level": 2},
        "unit_price": {"category": "医疗应用", "level": 3},
        "is_chronic": {"category": "健康状况", "level": 4,
                       "note": "慢病标记属健康状况（⏳ 待加密清单）"},
    },
    "policy_documents": {
        "id": {"category": "其他", "level": 1},
        "title": {"category": "公共卫生", "level": 1, "note": "公开政策文本"},
        "content": {"category": "公共卫生", "level": 1},
        "source": {"category": "公共卫生", "level": 1},
        "publish_date": {"category": "公共卫生", "level": 1},
        "category": {"category": "公共卫生", "level": 1},
        "tags": {"category": "公共卫生", "level": 1},
    },
    "data_authorizations": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "data_type": {"category": "其他", "level": 2, "note": "授权的数据类型"},
        "authorized_agent": {"category": "其他", "level": 1, "note": "被授权智能体"},
        "authorized_at": {"category": "其他", "level": 1},
        "expires_at": {"category": "其他", "level": 1},
        "is_active": {"category": "其他", "level": 1},
    },
    "eeg_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "session_id": {"category": "其他", "level": 1},
        "recorded_at": {"category": "其他", "level": 1},
        "duration_seconds": {"category": "其他", "level": 1},
        "mental_state": {"category": "健康状况", "level": 4, "note": "心理状态（单独同意）"},
        "mental_state_label": {"category": "健康状况", "level": 4},
        "avg_band_powers": {"category": "健康状况", "level": 4, "note": "脑电频段功率"},
        "metrics": {"category": "健康状况", "level": 4, "note": "健康指标+情绪"},
        "alert_count": {"category": "健康状况", "level": 2, "note": "预警数量（计数）"},
        "policy_link_count": {"category": "其他", "level": 1},
        "summary": {"category": "健康状况", "level": 4},
    },
    "imaging_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "study_id": {"category": "其他", "level": 1},
        "study_type": {"category": "医疗应用", "level": 2, "note": "检查类型"},
        "seed": {"category": "其他", "level": 1, "note": "合成参数"},
        "recorded_at": {"category": "其他", "level": 1},
        "findings": {"category": "健康状况", "level": 4, "note": "AI 检测发现（单独同意）"},
        "final_findings": {"category": "健康状况", "level": 4, "note": "医生复核标注"},
        "report": {"category": "健康状况", "level": 4, "note": "结构化报告"},
        "risk_level": {"category": "健康状况", "level": 4, "note": "风险等级"},
        "policy_link_count": {"category": "其他", "level": 1},
    },
    "cancer_prediction_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "recorded_at": {"category": "其他", "level": 1},
        "engine": {"category": "其他", "level": 1},
        "mode": {"category": "其他", "level": 1},
        "source": {"category": "其他", "level": 1},
        "result": {"category": "健康状况", "level": 4, "note": "泛癌风险报告（单独同意；⏳ 待加密清单）"},
    },
    "body_documents": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "filename": {"category": "个人属性", "level": 3, "note": "文件名可能含个人标识"},
        "mime_type": {"category": "其他", "level": 1},
        "doc_kind": {"category": "医疗应用", "level": 2, "note": "CT/MRI/病历类型"},
        "extracted_text": {"category": "健康状况", "level": 4, "note": "医疗资料解析文本（⏳ 待加密清单）"},
        "uploaded_at": {"category": "其他", "level": 1},
    },
    "body_records": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "organ": {"category": "健康状况", "level": 4, "note": "器官/部位属健康信息"},
        "description": {"category": "健康状况", "level": 4, "encrypted": True,
                        "note": "原文转述；AES-GCM 已落地"},
        "raw_excerpt": {"category": "健康状况", "level": 4, "encrypted": True,
                        "note": "原文片段；AES-GCM 已落地"},
        "event_date": {"category": "健康状况", "level": 2},
        "source_type": {"category": "其他", "level": 1},
        "source_label": {"category": "其他", "level": 1},
        "source_ref": {"category": "其他", "level": 1},
        "document_id": {"category": "其他", "level": 1},
        "batch_id": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "body_archive_files": {
        "id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1},
        "stored_name": {"category": "其他", "level": 1},
        "filename": {"category": "个人属性", "level": 3, "note": "文件名可能含个人标识"},
        "mime_type": {"category": "其他", "level": 1},
        "category": {"category": "其他", "level": 1},
        "note": {"category": "健康状况", "level": 3, "note": "用户备注"},
        "size_bytes": {"category": "其他", "level": 1},
        "content": {"category": "健康状况", "level": 4, "note": "原始医疗附件二进制（⏳ 生产迁对象存储+加密）"},
        "uploaded_at": {"category": "其他", "level": 1},
    },
    "federation_jobs": {
        "id": {"category": "其他", "level": 1},
        "task": {"category": "公共卫生", "level": 1, "note": "任务名"},
        "rounds": {"category": "其他", "level": 1},
        "local_epochs": {"category": "其他", "level": 1},
        "dp_sigma": {"category": "其他", "level": 1, "note": "差分隐私参数"},
        "clip_norm": {"category": "其他", "level": 1},
        "status": {"category": "其他", "level": 1},
        "result": {"category": "公共卫生", "level": 2, "note": "聚合 AUC，不含原始个人数据"},
        "duration_ms": {"category": "其他", "level": 1},
        "prev_hash": {"category": "其他", "level": 1, "note": "存证链哈希"},
        "event_hash": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "federated_nodes": {
        "id": {"category": "其他", "level": 1},
        "name": {"category": "卫生资源", "level": 2, "note": "医院/节点名称"},
        "token_hash": {"category": "其他", "level": 3,
                       "note": "节点凭证 sha256 单向哈希，非明文可逆"},
        "status": {"category": "其他", "level": 1},
        "last_seen_at": {"category": "其他", "level": 1,
                         "note": "节点最后活跃时间（节点面认证请求刷新，P1.3 掉线可见性）"},
        "created_at": {"category": "其他", "level": 1},
    },
    "distributed_federation_jobs": {
        "id": {"category": "其他", "level": 1},
        "task": {"category": "公共卫生", "level": 1, "note": "任务名"},
        "rounds": {"category": "其他", "level": 1},
        "local_epochs": {"category": "其他", "level": 1},
        "dp_sigma": {"category": "其他", "level": 1, "note": "差分隐私参数"},
        "clip_norm": {"category": "其他", "level": 1},
        "status": {"category": "其他", "level": 1},
        "current_round": {"category": "其他", "level": 1},
        "expected_nodes": {"category": "其他", "level": 2, "note": "被指派节点 id 清单"},
        "scaler_state": {"category": "公共卫生", "level": 2,
                         "note": "聚合联邦统计（均值/方差），不含个体记录"},
        "global_coef": {"category": "公共卫生", "level": 2,
                        "note": "聚合模型参数，非个体数据"},
        "result": {"category": "公共卫生", "level": 2, "note": "加权 AUC 等聚合指标"},
        "duration_ms": {"category": "其他", "level": 1},
        "prev_hash": {"category": "其他", "level": 1, "note": "存证链哈希"},
        "event_hash": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "distributed_job_updates": {
        "id": {"category": "其他", "level": 1},
        "job_id": {"category": "其他", "level": 1},
        "node_id": {"category": "其他", "level": 1},
        "round": {"category": "其他", "level": 1, "note": "轮次（0=联邦统计阶段）"},
        "kind": {"category": "其他", "level": 1, "note": "moments/update"},
        "payload_enc": {"category": "公共卫生", "level": 3, "encrypted": True,
                        "note": "节点参数 AES-256-GCM 信封（应用层加密，密钥 FEDERATION_PARAM_KEY）；"
                                "仅含模型参数/聚合统计，无行级数据"},
        "n_samples": {"category": "公共卫生", "level": 2, "note": "节点样本量（聚合量）"},
        "local_auc": {"category": "公共卫生", "level": 2, "note": "节点本地验证 AUC（聚合指标）"},
        "created_at": {"category": "其他", "level": 1},
    },
    "data_products": {
        "id": {"category": "其他", "level": 1},
        "name": {"category": "其他", "level": 1, "note": "公开目录商品名"},
        "provider": {"category": "其他", "level": 1, "note": "机构提供方"},
        "data_type": {"category": "其他", "level": 1},
        "description": {"category": "其他", "level": 1, "note": "仅去标识化统计产物上架"},
        "sample_count": {"category": "其他", "level": 1},
        "price": {"category": "其他", "level": 1},
        "price_unit": {"category": "其他", "level": 1},
        "privacy_tech": {"category": "其他", "level": 1},
        "status": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "data_transactions": {
        "id": {"category": "其他", "level": 1},
        "product_id": {"category": "其他", "level": 1},
        "product_name": {"category": "其他", "level": 1},
        "buyer": {"category": "其他", "level": 2, "note": "买方机构名"},
        "amount": {"category": "其他", "level": 2, "note": "交易金额"},
        "status": {"category": "其他", "level": 1},
        "revenue_provider": {"category": "其他", "level": 2, "note": "分账"},
        "revenue_platform": {"category": "其他", "level": 2},
        "revenue_contributor": {"category": "其他", "level": 2},
        "purpose": {"category": "其他", "level": 2, "note": "用途限定"},
        "prev_hash": {"category": "其他", "level": 1},
        "event_hash": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
    },
    "payment_orders": {
        "id": {"category": "其他", "level": 1},
        "order_no": {"category": "医疗应用", "level": 2, "note": "订单号"},
        "kind": {"category": "其他", "level": 1},
        "ref_id": {"category": "其他", "level": 1},
        "user_id": {"category": "其他", "level": 1, "note": "下单用户标识"},
        "subject": {"category": "医疗应用", "level": 1, "note": "商品/服务名"},
        "amount_cents": {"category": "医疗应用", "level": 3, "note": "支付金额（财产类）"},
        "status": {"category": "其他", "level": 1},
        "gateway": {"category": "其他", "level": 1},
        "qr_code": {"category": "其他", "level": 1, "note": "收款二维码"},
        "trade_no": {"category": "医疗应用", "level": 3, "note": "支付宝交易号（⏳ 待加密清单）"},
        "paid_at": {"category": "其他", "level": 1},
        "pay_proof": {"category": "其他", "level": 1, "note": "存证哈希"},
        "created_at": {"category": "其他", "level": 1},
    },
    "chain_anchors": {
        "id": {"category": "其他", "level": 1},
        "created_at": {"category": "其他", "level": 1},
        "tip_hash": {"category": "其他", "level": 1, "note": "链尖哈希（不含个人数据）"},
        "fed_tip": {"category": "其他", "level": 1},
        "market_tip": {"category": "其他", "level": 1},
        "event_count": {"category": "其他", "level": 1},
        "tsa_url": {"category": "其他", "level": 1},
        "status": {"category": "其他", "level": 1},
        "ts_token_b64": {"category": "其他", "level": 1, "note": "TSA 时间戳令牌"},
        "error": {"category": "其他", "level": 1},
    },
    "access_denial_logs": {
        "id": {"category": "其他", "level": 1},
        "ts": {"category": "其他", "level": 1},
        "method": {"category": "其他", "level": 1},
        "path": {"category": "其他", "level": 2, "note": "路径可能含 user_id（内部标识）"},
        "target_user_id": {"category": "其他", "level": 1},
        "status_code": {"category": "其他", "level": 1},
        "reason": {"category": "其他", "level": 1},
        "client_ip": {"category": "个人属性", "level": 3, "note": "IP 属个人信息（安全审计法定留存）"},
        "token_present": {"category": "其他", "level": 1},
    },
}


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------

def get_field_level(table: str, field: str) -> int | None:
    """查字段分级标签（1-5）；未登记返回 None。"""
    entry = _REGISTRY.get(table, {}).get(field)
    return entry["level"] if entry else None


def fields_by_level(level: int) -> dict[str, list[str]]:
    """按级别反查字段清单（脱敏/加密专项治理用）。"""
    out: dict[str, list[str]] = {}
    for table, fields in _REGISTRY.items():
        hit = [f for f, meta in fields.items() if meta["level"] == level]
        if hit:
            out[table] = hit
    return out


def pending_encryption_fields() -> dict[str, list[str]]:
    """4 级字段中尚未落地存储加密的清单（待加密清单，管理办法第 6 节跟踪）。"""
    out: dict[str, list[str]] = {}
    for table, fields in _REGISTRY.items():
        hit = [f for f, meta in fields.items()
               if meta["level"] == 4 and not meta.get("encrypted")]
        if hit:
            out[table] = hit
    return out


def registry() -> dict[str, dict[str, dict]]:
    """只读台账（返回浅拷贝防误改）。"""
    return {t: dict(f) for t, f in _REGISTRY.items()}
