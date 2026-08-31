from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    age = Column(Integer, nullable=False)
    gender = Column(String(10), nullable=False)
    city = Column(String(50), nullable=False)
    insurance_type = Column(String(50), nullable=False)
    employee_status = Column(String(30), nullable=False)
    # 邮箱注册登录（Demo 演示用户无邮箱，字段可空）
    email = Column(String(255), unique=True, index=True, nullable=True)
    # PBKDF2-SHA256 哈希，格式：pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    password_hash = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    insurance_records = relationship("InsuranceRecord", back_populates="user")
    medical_records = relationship("MedicalRecord", back_populates="user")
    medication_records = relationship("MedicationRecord", back_populates="user")
    authorizations = relationship("DataAuthorization", back_populates="user")
    eeg_records = relationship("EEGRecord", back_populates="user")
    imaging_records = relationship("ImagingRecord", back_populates="user")
    cancer_records = relationship("CancerPredictionRecord", back_populates="user")
    body_documents = relationship("BodyDocument", back_populates="user")
    body_records = relationship("BodyRecord", back_populates="user")
    body_archive_files = relationship("BodyArchiveFile", back_populates="user")
    chat_conversations = relationship("ChatConversation", back_populates="user")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(100), nullable=False, default="新对话")
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    user = relationship("User", back_populates="chat_conversations")
    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.id",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(String(36), ForeignKey("chat_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    agent_type = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    conversation = relationship("ChatConversation", back_populates="messages")


class InsuranceRecord(Base):
    __tablename__ = "insurance_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    base_amount = Column(Float, nullable=False)
    personal_amount = Column(Float, nullable=False)
    company_amount = Column(Float, nullable=False)

    user = relationship("User", back_populates="insurance_records")


class MedicalRecord(Base):
    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    hospital = Column(String(100), nullable=False)
    department = Column(String(50), nullable=False)
    diagnosis = Column(String(200), nullable=False)
    visit_type = Column(String(30), nullable=False)
    total_cost = Column(Float, nullable=False)
    reimbursed_amount = Column(Float, nullable=False)

    user = relationship("User", back_populates="medical_records")


class MedicationRecord(Base):
    __tablename__ = "medication_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    medication_name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    is_chronic = Column(Boolean, default=False)

    user = relationship("User", back_populates="medication_records")


class PolicyDocument(Base):
    __tablename__ = "policy_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(200), nullable=False)
    content = Column(String, nullable=False)
    source = Column(String(100))
    publish_date = Column(DateTime)
    category = Column(String(50))
    tags = Column(String(200))


class DataAuthorization(Base):
    __tablename__ = "data_authorizations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    data_type = Column(String(50), nullable=False)
    authorized_agent = Column(String(100), nullable=False)
    authorized_at = Column(DateTime, default=lambda: datetime.now(UTC))
    expires_at = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)

    user = relationship("User", back_populates="authorizations")


class EEGRecord(Base):
    """脑电采集记录（BCI×医保创新模块）

    存储每次 EEG 会话的评估结果摘要，用于历史趋势分析。
    完整波形数据较大，不入库（实时生成即可）。
    """

    __tablename__ = "eeg_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String(80), nullable=False, index=True)
    recorded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    duration_seconds = Column(Integer, nullable=False, default=4)
    mental_state = Column(String(30), nullable=False)
    mental_state_label = Column(String(30), nullable=False)
    # 五频段平均功率（JSON 字符串）
    avg_band_powers = Column(Text, nullable=False)
    # 四维健康指标 + 情绪（JSON 字符串）
    metrics = Column(Text, nullable=False)
    # 预警数量
    alert_count = Column(Integer, default=0)
    # 联动政策数量
    policy_link_count = Column(Integer, default=0)
    # 摘要
    summary = Column(Text, default="")

    user = relationship("User", back_populates="eeg_records")


class ImagingRecord(Base):
    """医学影像检查记录（瓯医数链 影像引擎）

    存储每次影像 AI 分析会话的结果摘要（影像数据以确定性合成参数
    study_type + seed + findings 可随时复现，不存大体积 base64）。
    """

    __tablename__ = "imaging_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    study_id = Column(String(80), nullable=False, index=True)
    study_type = Column(String(30), nullable=False)
    seed = Column(Integer, nullable=False, default=0)
    recorded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    # AI 检测发现（JSON 字符串）
    findings = Column(Text, nullable=False)
    # 医生复核后标注（JSON 字符串）
    final_findings = Column(Text, nullable=True)
    # 结构化报告（JSON 字符串）
    report = Column(Text, nullable=True)
    # 风险等级（低/中/高/待复核）
    risk_level = Column(String(20), default="待复核")
    # 联动政策数量
    policy_link_count = Column(Integer, default=0)

    user = relationship("User", back_populates="imaging_records")


class CancerPredictionRecord(Base):
    """泛癌卫士预测存档（Oncoformer）

    存储每次泛癌风险预测的结果摘要（风险分数 JSON），用于监管审计
    与"模型作为数据产品被消费"的存证叙事。
    """

    __tablename__ = "cancer_prediction_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    recorded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    # oncoformer（真模型实时）/ oncoformer-precomputed（预计算队列）
    engine = Column(String(40), default="oncoformer")
    # ehr_only / fused / img_only / cohort_fallback
    mode = Column(String(20), default="ehr_only")
    # synthetic_visits（模拟就诊序列）/ compass_cohort（真实脱敏队列）
    source = Column(String(30), default="synthetic_visits")
    # 完整风险报告（JSON 字符串）
    result = Column(Text, nullable=False)

    user = relationship("User", back_populates="cancer_records")


class BodyDocument(Base):
    """用户上传的医疗资料存档（档案管家）

    只保存解析出的文本（不存文件二进制），用于版本追溯：每条 BodyRecord 可回指来源文档。
    """

    __tablename__ = "body_documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(200), nullable=False)
    mime_type = Column(String(80), default="")
    # CT报告 / MRI报告 / 病历文本 / 其他
    doc_kind = Column(String(30), default="其他")
    extracted_text = Column(Text, default="")
    uploaded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    user = relationship("User", back_populates="body_documents")
    records = relationship("BodyRecord", back_populates="document")


class BodyRecord(Base):
    """人体健康档案记录（档案管家）— 只增不删

    每条记录 = 用户在对话/资料中**明确陈述**的一条部位相关信息（原文转述 + 原文片段）。
    新信息永远追加，不覆盖历史；batch_id 标记同一次归档周期（版本分组）。
    不含任何推断或诊断字段。
    """

    __tablename__ = "body_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 器官/部位 key（见 services/body/taxonomy.py）
    organ = Column(String(40), nullable=False, index=True)
    # 原文转述
    description = Column(Text, nullable=False)
    # 原文片段（逐字）
    raw_excerpt = Column(Text, default="")
    # 检查/发生时间，允许不完整："2026-02" / "2026-07-15" / ""
    event_date = Column(String(10), default="")
    # chat | upload
    source_type = Column(String(10), nullable=False)
    # 对话输入 / CT报告 / MRI报告 / 病历文本 / 其他
    source_label = Column(String(30), nullable=False)
    # conversation_id 或 文件名
    source_ref = Column(String(200), default="")
    document_id = Column(Integer, ForeignKey("body_documents.id"), nullable=True)
    batch_id = Column(String(40), default="", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    user = relationship("User", back_populates="body_records")
    document = relationship("BodyDocument", back_populates="records")


class BodyArchiveFile(Base):
    """数字人体档案原始附件。

    测试阶段直接存入数据库，确保图片、PDF、CSV 可按用户隔离预览和下载；
    生产环境应迁移到受控对象存储并保留本表元数据与审计关联。
    """

    __tablename__ = "body_archive_files"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    stored_name = Column(String(160), nullable=False)
    filename = Column(String(200), nullable=False)
    mime_type = Column(String(100), nullable=False)
    category = Column(String(40), default="原始资料")
    note = Column(String(200), default="")
    size_bytes = Column(Integer, nullable=False)
    content = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    user = relationship("User", back_populates="body_archive_files")


class FederationJob(Base):
    """联邦学习任务（瓯医数链底座）。

    event_hash 审计存证链：每个任务的存证哈希由上一任务哈希 + 本任务
    摘要串联计算（sha256），任何结果篡改都会导致链条断裂。
    """

    __tablename__ = "federation_jobs"

    id = Column(String(36), primary_key=True)
    task = Column(String(100), nullable=False, default="hf_readmission")
    rounds = Column(Integer, nullable=False, default=12)
    local_epochs = Column(Integer, nullable=False, default=3)
    dp_sigma = Column(Float, nullable=False, default=0.0)
    clip_norm = Column(Float, nullable=False, default=1.0)
    status = Column(String(20), nullable=False, default="running")  # running/done/failed
    result = Column(Text, nullable=True)  # JSON：AUC 曲线、最终 AUC、逐院对比
    duration_ms = Column(Integer, nullable=True)
    prev_hash = Column(String(64), nullable=True)
    event_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class DataProduct(Base):
    """数据产品（数据要素流通目录中的上架商品）。"""

    __tablename__ = "data_products"

    id = Column(String(36), primary_key=True)
    name = Column(String(120), nullable=False)
    provider = Column(String(60), nullable=False)  # 提供方：医院/平台/应用
    data_type = Column(String(40), nullable=False)  # 数据集/模型API/治理产物/算法服务
    description = Column(Text, default="")
    sample_count = Column(Integer, default=0)
    price = Column(Integer, default=0)  # 元
    price_unit = Column(String(20), default="套")  # 套/年/次
    privacy_tech = Column(String(120), default="")  # 隐私技术标签
    status = Column(String(20), default="在售")  # 在售/已下架
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class DataTransaction(Base):
    """数据要素交易（含授权审批与审计存证链）。"""

    __tablename__ = "data_transactions"

    id = Column(String(36), primary_key=True)
    product_id = Column(String(36), ForeignKey("data_products.id"), nullable=False, index=True)
    product_name = Column(String(120), nullable=False)
    buyer = Column(String(80), nullable=False)  # 买方：应用生态/机构
    amount = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="待授权")  # 待授权/已成交/已驳回
    revenue_provider = Column(Integer, default=0)  # 医院 70%
    revenue_platform = Column(Integer, default=0)  # 平台 20%
    revenue_contributor = Column(Integer, default=0)  # 数据贡献者 10%
    purpose = Column(String(200), default="")  # 用途限定
    prev_hash = Column(String(64), nullable=True)
    event_hash = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)


class AccessDenialLog(Base):
    """越权访问审计（P0 生产鉴权：严格模式下 401/403 拒绝记录落库）。"""

    __tablename__ = "access_denial_logs"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    target_user_id = Column(String(50), default="")  # 被尝试访问的用户（列表型端点为空）
    status_code = Column(Integer, nullable=False)  # 401 / 403
    reason = Column(String(60), nullable=False)  # missing_session/cross_user_access/…
    client_ip = Column(String(64), default="")
    token_present = Column(Boolean, default=False)
