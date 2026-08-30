"""
MedSignal - 智能体编排服务

增强版编排器，集成：
- LLMService：用于意图识别和对话生成
- KnowledgeBase：用于政策知识检索
- 降级机制：服务不可用时回退到关键词匹配和 mock 数据
"""

import asyncio
import logging
from typing import Any

from app.config import settings
from app.services.body import taxonomy as body_taxonomy
from app.services.knowledge_base import KnowledgeBase, SearchResult
from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class Orchestrator:
    """智能体编排服务：意图识别、路由分发、结果聚合

    优先使用 LLM 进行意图识别和对话生成，
    优先使用 KnowledgeBase 进行政策检索，
    服务不可用时自动降级到关键词匹配和 mock 数据。
    """

    # 关键词意图映射（降级方案）
    AGENT_KEYWORDS: dict[str, list[str]] = {
        "general": ["你好", "您好", "嗨", "在吗", "你是谁", "能做什么", "怎么用", "帮助", "谢谢", "再见"],
        "coverage": ["报销", "待遇", "报销比例", "起付线", "封顶线", "医保卡", "个人账户", "缴费",
                     "能报多少", "报多少", "账户余额", "参保", "权益"],
        "claims": ["理赔", "报销流程", "发票", "OCR", "上传", "预审", "报销材料",
                   "票据", "报销单", "提交报销"],
        "governance": ["病历治理", "病历", "脱敏", "结构化", "PHI", "病历质控", "治理病历", "病历脱敏", "入院记录"],
        "health_profile": ["健康", "体检", "画像", "慢病", "用药", "趋势", "预警",
                           "健康风险", "身体状况", "身体", "不舒服", "症状", "血压", "血糖"],
        "policy": ["政策", "规定", "通知", "办法", "文件", "异地", "省钱",
                   "惠民", "享受什么", "能享受", "门诊慢病"],
        "security": ["授权", "隐私", "数据安全", "审计", "权限", "我的数据"],
        "eeg": ["脑电", "EEG", "压力", "睡眠质量", "注意力", "认知负荷", "情绪",
                "放松", "专注", "疲劳", "焦虑", "心理", "脑机", "BCI", "波形",
                "脑电健康", "脑电评估", "脑电分析"],
        "imaging": ["影像", "胸片", "X光", "X 光", "CT", "核磁", "MRI", "肺结节",
                    "病灶", "影像分析", "影像标注", "影像报告", "肺部扫描",
                    "脑部扫描", "影像检查", "医学影像"],
        # 档案管家：用户自述部位/症状/检查结果，或查看/对比自己的健康档案
        # 只用器官"标签"而非全部别名，避免任何提到部位的句子都压过其他意图（其他意图仍有常驻归档钩子兜底）
        "body": ["档案", "记录一下", "帮我记", "对比", "复查", "查出", "确诊",
                 "疼", "痛", "不适", "结节"]
                + [label for label, _ in body_taxonomy.ORGANS.values() if "/" not in label],
        # 数据管家：湖仓数据资产/智能查询/质量血缘（避开与 security “我的数据” 的碰撞，用数据类词组）
        "data": ["查数据", "数据库", "湖仓", "数据湖", "数据目录", "数据资产", "数据质量",
                 "数据血缘", "有多少条", "多少条记录", "统计", "汇总", "数据分析",
                 "查询数据", "数据查询", "TOP10", "用药排行", "缴费统计"],
        # 药品卫士：拍照识别药品/核验批准文号与有效期（避开 health_profile 的“用药”）
        "drug": ["药品识别", "扫药", "扫描药品", "拍照识别", "药盒", "药品包装",
                 "批准文号", "国药准字", "药品有效期", "药品过期", "这个药", "是什么药"],
    }

    # Mock 数据（降级方案）
    MOCK_RESPONSES: dict[str, dict[str, Any]] = {
        "coverage": {
            "response": "根据您的参保信息，城镇职工医保门诊报销比例为70%，住院报销比例为85%。",
            "data": {"reimbursement_rate": 0.70, "deductible": 800},
        },
        "claims": {
            "response": "已为您启动理赔预审流程，请上传相关发票和病历材料。",
            "data": {"pre_review_status": "pending", "required_docs": ["发票原件", "处方复印件"]},
        },
        "health_profile": {
            "response": "您的健康画像已生成，综合健康评分70分，建议关注慢性病管理。",
            "data": {"health_score": 70, "chronic_diseases": ["高血压"]},
        },
        "policy": {
            "response": "为您匹配到2条相关政策，门诊统筹报销比例已提升至70%。",
            "data": {"matched_count": 2, "top_policy": "浙江省城镇职工基本医疗保险门诊统筹办法"},
        },
        "security": {
            "response": "您的数据授权状态正常，当前有2项有效授权。",
            "data": {"active_authorizations": 2},
        },
        "eeg": {
            "response": "已为您完成脑电健康评估。基于 EEG 五频段功率分析，当前压力指数、注意力、睡眠质量、认知负荷四维指标正常。脑电异常将自动联动医保政策推荐。",
            "data": {"mental_state": "relaxed", "stress_index": 20, "attention_index": 50},
        },
        "imaging": {
            "response": "已为您完成医学影像 AI 分析。AI 引擎对影像进行病灶检测与预标注，结果需由医师复核确认。检测发现将联动医保检查报销政策推荐。",
            "data": {"study_types": ["chest_xray", "lung_ct", "brain_mri"], "ai_findings": 0},
        },
        "body": {
            "response": "档案管家已就绪：您可以直接告诉我身体某个部位的情况（如“2026年2月查出肺结节”），"
                        "或上传 CT/MRI 报告，我会按部位整理归档，随时可查看、对比。只整理您提供的信息，不做诊断。",
            "data": {"body_updates": [], "body_focus": None},
        },
        "data": {
            "response": "已为您接入湖仓一体数据服务。仓层包含参保缴费、就医、用药、脑电、影像等主题表，"
                        "支持自然语言智能查询（如“帮我汇总就医费用”），每次查询均展示 SQL 与统计口径。",
            "data": {"warehouse_tables": 10, "query_source": "template"},
        },
        "drug": {
            "response": "药品卫士已就绪：请通过首页“药品识别”入口拍摄药盒或包装照片，"
                        "我会识别通用名、批准文号与有效期，核查与您现有用药的相互作用。是否加入用药记录由您决定。",
            "data": {"scan_entry": "/api/drugs/scan", "recognition_modes": ["vision", "ocr_llm"]},
        },
    }

    def __init__(self):
        """初始化编排器，懒加载 LLM 和知识库服务"""
        self._llm: LLMService | None = None
        self._kb: KnowledgeBase | None = None
        self._services_initialized = False
        # user_id -> 活跃业务流程（如 claims_prereview），用于歧义消息的上下文路由
        self._active_flows: dict[str, str] = {}
        # user_id -> 最近一次确认路由的智能体，用于追问/细节类消息的连续性路由
        self._last_agent: dict[str, str] = {}

    async def initialize_services(self) -> None:
        """初始化 LLM 和知识库服务

        应在应用启动时调用。初始化失败不影响基本功能，
        会自动降级到关键词匹配和 mock 数据。
        """
        if self._services_initialized:
            return

        if settings.DEMO_OFFLINE:
            logger.info("DEMO_OFFLINE=1：跳过 LLM/知识库初始化，使用离线降级模式")
            self._llm = None
            self._kb = None
            self._services_initialized = True
            return

        # 初始化 LLM 服务（主力：aiping 网关 Kimi-K3，备选：阿里云 DashScope）
        try:
            self._llm = LLMService(
                api_key=settings.LLM_API_KEY,
                base_url=settings.LLM_BASE_URL,
                model=settings.LLM_MODEL,
                fallback_api_key=settings.DASHSCOPE_API_KEY,
                fallback_base_url=settings.DASHSCOPE_BASE_URL,
                fallback_model=settings.DASHSCOPE_MODEL,
            )
            if self._llm.is_available:
                logger.info("LLM 服务初始化成功")
            else:
                logger.warning("LLM 服务不可用，将使用关键词匹配降级方案")
                self._llm = None
        except Exception as e:
            logger.warning("LLM 服务初始化失败: %s，将使用降级方案", e)
            self._llm = None

        # 初始化知识库服务
        # P0-4 修复：embedding 改用 ChromaDB 默认 sentence-transformers 离线模型
        # 原配置用 SenseNova base_url + text-embedding-3-small（OpenAI 模型名），商汤不支持会静默降级
        # 离线模型更稳定，无网络依赖，杜绝现场演示 embedding API 失败风险
        try:
            self._kb = KnowledgeBase(
                embedding_api_key="",      # 留空 → 自动用 ChromaDB 默认嵌入
                embedding_base_url="",
                embedding_model="",
            )
            # 初始化含 ChromaDB 集合创建（首次可能触发 ONNX 模型下载），
            # 超时则降级 KB=None，不影响启动与其他功能
            await asyncio.wait_for(
                self._kb.initialize(persist_dir=settings.CHROMA_PERSIST_DIR),
                timeout=60.0,
            )
            stats = await self._kb.get_stats()
            if stats.get("total_chunks", 0) > 0:
                logger.info("知识库服务初始化成功，已有 %d 个文本块", stats["total_chunks"])
            else:
                logger.warning("知识库为空，请先运行 build_knowledge_base.py 构建索引")
        except Exception as e:
            logger.warning("知识库服务初始化失败: %s，将使用 mock 数据降级方案", e)
            self._kb = None

        self._services_initialized = True

    # ------------------------------------------------------------------
    # 意图识别
    # ------------------------------------------------------------------

    async def intent_recognition(self, message: str) -> str:
        """根据用户消息识别意图，返回对应的智能体类型

        关键词命中时直接判定（毫秒级）；仅零命中时才用 LLM 消歧，
        避免每条消息都多一次 20s+ 的 LLM 往返。
        """
        if self.has_keyword_intent(message):
            return self._keyword_intent(message)

        # 零关键词命中：优先使用 LLM 消歧（模糊表达），置信度不足则兜底 coverage
        if self._llm is not None:
            try:
                intent_result = await self._llm.extract_intent(message)
                intent = intent_result.get("intent", "")
                confidence = intent_result.get("confidence", 0)

                # 置信度足够高时使用 LLM 结果
                if intent in self.AGENT_KEYWORDS and confidence >= 0.5:
                    logger.info("LLM 意图识别: %s (confidence=%.2f)", intent, confidence)
                    return intent

                logger.info("LLM 意图置信度不足 (%.2f)，降级到关键词匹配", confidence)
            except Exception as e:
                logger.warning("LLM 意图识别异常: %s，降级到关键词匹配", e)

        # 降级：关键词匹配
        return self._keyword_intent(message)

    def _keyword_intent(self, message: str) -> str:
        """基于关键词的意图识别（降级方案）"""
        scores: dict[str, int] = {}
        for agent_type, keywords in self.AGENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message)
            scores[agent_type] = score

        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return "general"
        return best

    # ------------------------------------------------------------------
    # 对话上下文感知：连续性 / 意图消歧 / 资料感知
    # ------------------------------------------------------------------

    _READING_VERBS = ("读到", "看到", "解读", "解释", "分析", "里面", "内容", "信息",
                      "说了啥", "写了啥", "啥意思", "这些", "刚才", "上传的", "那些")

    # 追问/要求展开类信号词（零关键词命中时用于连续性路由）
    _FOLLOWUP_MARKERS = ("详细", "具体", "细节", "展开", "继续", "接着", "再多", "更多",
                         "为什么", "怎么算", "怎么来的", "依据", "说说", "讲讲", "解释",
                         "啥意思", "上面", "刚才", "这个", "这些", "那么")

    # 助手回复中的智能体署名 → 智能体类型（前端 history 携带完整回复文本）
    _AGENT_SIGNATURES = (
        ("报销助手", "claims"),
        ("档案管家", "body"),
        ("权益管家", "coverage"),
        ("政策参谋", "policy"),
        ("健康管家", "health_profile"),
        ("药品卫士", "drug"),
    )

    def has_keyword_intent(self, message: str) -> bool:
        """消息自身是否命中任一智能体关键词。"""
        return any(kw in message for kws in self.AGENT_KEYWORDS.values() for kw in kws)

    def note_intent(self, user_id: str | None, intent: str) -> None:
        """记录用户活跃业务流程与最近智能体：
        claims 意图出现后，后续歧义消息优先路由回报销流程；
        最近智能体用于追问/细节类消息的连续性路由。"""
        if not user_id:
            return
        if intent == "claims":
            self._active_flows[user_id] = "claims_prereview"
        if intent:
            self._last_agent[user_id] = intent

    def followup_intent(
        self, message: str, history: list[dict] | None = None, user_id: str | None = None,
    ) -> str | None:
        """追问/细节类消息的连续性路由。

        仅在消息零关键词命中时介入：消息含追问信号词，且上一轮助手回复带智能体署名
        （如【报销助手】），则路由回同一智能体，保持对话连续性。不适用返回 None。
        """
        if self.has_keyword_intent(message):
            return None
        if not any(w in message for w in self._FOLLOWUP_MARKERS):
            return None
        for h in reversed(history or []):
            if h.get("role") != "assistant":
                continue
            content = str(h.get("content") or "")
            for signature, agent in self._AGENT_SIGNATURES:
                if signature in content:
                    return agent
            break  # 只看最近一轮助手回复，无署名则不介入
        return self._last_agent.get(user_id) if user_id else None

    def context_intent(
        self, message: str, history: list[dict] | None = None,
        has_recent_docs: bool = False, user_id: str | None = None,
    ) -> str | None:
        """上下文感知意图消歧。

        仅在消息本身零关键词命中（否则将兜底到 coverage）时介入：
        最近历史含上传动作或用户有近期存档资料，且当前消息为阅读/指代类问句，
        则路由到读资料的智能体（报销流程活跃时优先报销助手）。不适用返回 None。
        """
        scores = {a: sum(1 for kw in kws if kw in message) for a, kws in self.AGENT_KEYWORDS.items()}
        if scores[max(scores, key=scores.get)] > 0:
            return None  # 消息自身有明确意图，走正常路由

        recent_upload = any(
            "上传" in str(h.get("content") or "")
            for h in (history or [])[-4:] if h.get("role") == "user"
        )
        if not (recent_upload or has_recent_docs):
            return None
        if not any(w in message for w in self._READING_VERBS):
            return None
        if user_id and self._active_flows.get(user_id) == "claims_prereview":
            return "claims"
        # 无内存流程状态时（如服务重启后），从最近助手回复的署名推断归属智能体
        for h in reversed(history or []):
            if h.get("role") != "assistant":
                continue
            content = str(h.get("content") or "")
            for signature, agent in self._AGENT_SIGNATURES:
                if signature in content:
                    return agent
            break
        return "body"

    @staticmethod
    def format_history_block(history: list[dict] | None, max_turns: int = 8) -> str:
        """将前端携带的 history 压缩为提示词上下文块（指代消解/连续性）。"""
        if not history:
            return ""
        lines = [
            f"{'用户' if h.get('role') == 'user' else '助手'}: {str(h.get('content') or '')[:200]}"
            for h in history[-max_turns:]
        ]
        return "最近对话历史（结合上下文理解用户当前问题）：\n" + "\n".join(lines)

    async def recent_documents_context(self, db, user_id: str | None,
                                       limit: int = 5, within_minutes: int = 120) -> str:
        """用户最近上传资料上下文块（文件名+类型+原文摘录），供各智能体引用解读。"""
        if db is None or not user_id:
            return ""
        try:
            from app import crud
            docs = await crud.list_recent_body_documents(
                db, user_id, limit=limit, within_minutes=within_minutes,
            )
        except Exception as e:
            logger.warning("查询最近上传资料失败: %s", e)
            return ""
        if not docs:
            return ""
        lines = []
        for d in docs:
            excerpt = " ".join((d.extracted_text or "").split())[:400]
            lines.append(
                f"- 《{d.filename}》（{d.doc_kind}）：{excerpt}" if excerpt
                else f"- 《{d.filename}》（{d.doc_kind}）"
            )
        return "用户最近上传的医疗资料（档案管家存档，可据此引用解读，不得编造）：\n" + "\n".join(lines)

    def multi_intent_recognition(self, message: str) -> list[tuple[str, float]]:
        """多意图识别：返回 [(intent, weight), ...]，按权重降序

        用于复合问题（如"我父亲做心脏搭桥能报多少"→ coverage + policy + claims）。
        规则：所有命中关键词数 >=1 的意图都返回，权重 = 命中数/总命中数。
        """
        scores: dict[str, int] = {}
        for agent_type, keywords in self.AGENT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in message)
            if score > 0:
                scores[agent_type] = score

        if not scores:
            return [("general", 1.0)]

        total = sum(scores.values())
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        # 只保留权重 >= 0.2 的意图（避免噪音）
        result = [(intent, score / total) for intent, score in ranked if score / total >= 0.2]
        return result if result else [("general", 1.0)]

    async def handle_complex_query(
        self, message: str, user_id: str | None = None, user_profile: dict | None = None,
        history: list[dict] | None = None, extra_context: str | None = None,
        offline_claims_response: str | None = None,
    ) -> dict[str, Any]:
        """处理复合意图查询：并行调度多 Agent + 结果融合

        这是 P2-1 多智能体协作的核心。例如：
        "我父亲做心脏搭桥能报多少，有哪些政策能省 钱" → coverage + policy + claims 并行

        extra_context：对话历史+最近上传资料上下文块（与 /chat 同源），
        贯穿到各 Agent 与融合提示词；
        offline_claims_response：离线模式下报销助手的真实资料预审结果，
        命中 claims 意图时替换 mock，避免各智能体回复与用户资料脱节。
        """
        intents = self.multi_intent_recognition(message)
        logger.info("复合意图识别: %s", intents)

        # 单意图直接走 route_to_agent
        if len(intents) == 1:
            result = await self.route_to_agent(
                intents[0][0], message, user_id, user_profile, extra_context=extra_context,
            )
            return {**result, "agents_invoked": [intents[0][0]], "multi_agent": False}

        # 多意图：并行调度（asyncio.gather）+ 单 Agent 超时保护。
        # 在线模式推理模型单次生成实测 20–55s，携带大段资料上下文时可达 90s+，
        # 单 Agent 预算给足 120s；离线模式规则引擎毫秒级返回，不受影响。
        import asyncio

        async def _run_one(intent: str, weight: float) -> tuple[str, "dict | None"]:
            try:
                r = await asyncio.wait_for(
                    self.route_to_agent(intent, message, user_id, user_profile, extra_context=extra_context),
                    timeout=120.0,
                )
                return intent, r
            except TimeoutError:
                logger.warning("Agent %s 执行超时(120s)，跳过", intent)
                return intent, None
            except Exception as e:
                logger.error("Agent %s 执行失败: %s", intent, e)
                return intent, None

        dispatch_intents = intents[:3]
        agent_results: dict[str, dict] = {}
        # 离线模式 + 近期上传资料 + 命中 claims：报销助手直接给出真实预审，不再参与并行调度（避免 mock）
        if offline_claims_response and any(i == "claims" for i, _ in dispatch_intents):
            agent_results["claims"] = {
                "response": offline_claims_response,
                "agent_type": "claims_agent",
                "data": {"offline_review": True},
            }
            dispatch_intents = [(i, w) for i, w in dispatch_intents if i != "claims"]

        if dispatch_intents:
            gathered = await asyncio.gather(*[_run_one(i, w) for i, w in dispatch_intents])
            for intent, r in gathered:
                if r is not None:
                    agent_results[intent] = r

        # 若所有 Agent 都超时/失败，用兜底
        if not agent_results:
            return {
                "response": "抱歉，智能体处理超时，请稍后重试或简化您的问题。",
                "data": {"agents_invoked": [], "multi_agent": True, "timeout_fallback": True},
                "agents_invoked": [],
                "multi_agent": True,
                "intent_weights": [{"intent": i, "weight": round(w, 2)} for i, w in intents],
            }

        # 结果融合（带超时保护，超时降级到拼接；在线模式融合生成同样需要 20–55s，预算 90s）
        try:
            fused = await asyncio.wait_for(
                self._fuse_multi_agent_results(message, intents, agent_results, extra_context=extra_context),
                timeout=90.0,
            )
        except TimeoutError:
            logger.warning("LLM 融合超时(90s)，降级拼接")
            fused = self._fuse_fallback(intents, agent_results)

        # 防御：融合返回异常结构（如 None）时降级拼接，避免 TypeError 透传 500
        if not isinstance(fused, dict):
            logger.warning("LLM 融合返回非结构化结果，降级拼接")
            fused = self._fuse_fallback(intents, agent_results)

        return {
            **fused,
            "agents_invoked": list(agent_results.keys()),
            "multi_agent": True,
            "intent_weights": [{"intent": i, "weight": round(w, 2)} for i, w in intents],
        }

    def _fuse_fallback(self, intents: list[tuple[str, float]],
                       agent_results: dict[str, dict]) -> dict[str, Any]:
        """LLM 融合超时时的降级拼接（不调 LLM，避免再次超时）"""
        agent_names = {
            "coverage": "权益管家", "claims": "报销助手",
            "health_profile": "健康卫士", "policy": "政策参谋",
            "security": "安全守门", "eeg": "脑电卫士",
            "imaging": "影像卫士", "body": "档案管家", "data": "数据管家",
            "drug": "药品卫士",
        }
        parts = []
        for intent, result in agent_results.items():
            name = agent_names.get(intent, intent)
            resp = result.get("response", "")[:400]
            if resp:
                parts.append(f"**【{name}】**\n{resp}")
        return {
            "response": "\n\n---\n\n".join(parts) if parts else "暂无法处理该复合问题",
            "data": {"fused": False, "agent_count": len(agent_results), "fallback": True},
        }

    async def _fuse_multi_agent_results(
        self, message: str, intents: list[tuple[str, float]],
        agent_results: dict[str, dict], extra_context: str | None = None,
    ) -> dict[str, Any]:
        """融合多 Agent 结果为一段连贯回答，标注来源 Agent。"""
        agent_names = {
            "coverage": "权益管家", "claims": "报销助手",
            "health_profile": "健康卫士", "policy": "政策参谋",
            "security": "安全守门", "eeg": "脑电卫士",
            "imaging": "影像卫士", "body": "档案管家", "data": "数据管家",
            "drug": "药品卫士",
        }

        # 优先用 LLM 融合
        if self._llm is not None and agent_results:
            try:
                # 构建各 Agent 输出摘要
                agent_outputs = []
                for intent, result in agent_results.items():
                    name = agent_names.get(intent, intent)
                    resp = result.get("response", "")[:500]
                    agent_outputs.append(f"【{name}】{resp}")

                fusion_prompt = (
                    "你是MedSignal的编排智能体。以下是多个专业智能体对同一问题的回答，"
                    "请将它们融合成一段连贯、完整、不重复的回答。"
                    "保留各智能体的关键结论，用【智能体名】标注信息来源。"
                    "如果某些信息重复，合并表述。最后给出 1-2 条综合建议。"
                    "若提供了用户资料/对话上下文，回答必须贴合这些信息，不得编造。"
                )
                user_content = f"用户问题：{message}\n\n各智能体回答：\n\n" + "\n\n".join(agent_outputs)
                if extra_context:
                    user_content += f"\n\n{extra_context}"
                fused = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": fusion_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.4,
                )
                return {
                    "response": fused,
                    "data": {"fused": True, "agent_count": len(agent_results)},
                    "evidence": [
                        {"type": "agent_source", "agent": agent_names.get(i, i), "weight": round(w, 2)}
                        for i, w in intents if i in agent_results
                    ],
                }
            except Exception as e:
                logger.error("LLM 融合失败: %s，降级拼接", e)

        # 降级：直接拼接各 Agent 回答
        parts = []
        for intent, result in agent_results.items():
            name = agent_names.get(intent, intent)
            resp = result.get("response", "")
            if resp:
                parts.append(f"**【{name}】**\n{resp}")
        return {
            "response": "\n\n---\n\n".join(parts) if parts else "暂无法处理该复合问题",
            "data": {"fused": False, "agent_count": len(agent_results)},
        }

    # ------------------------------------------------------------------
    # 路由分发
    # ------------------------------------------------------------------

    async def route_to_agent(
        self, agent_type: str, message: str, user_id: str | None = None,
        user_profile: dict | None = None, extra_context: str = "",
    ) -> dict[str, Any]:
        """路由到对应智能体处理请求

        根据智能体类型选择不同的处理策略：
        - policy: 优先使用 KnowledgeBase 检索 + LLM 生成
        - health_profile: 优先使用 LLM 生成健康预警（可注入 user_profile 真实数据）
        - 其他: 使用 LLM 对话或降级到 mock 数据

        Args:
            user_profile: 可选的真实用户画像（由 Router 层从数据库查得后注入）。
                          若提供，health/coverage Agent 将基于真实数据分析。
        """
        self._last_user_profile = user_profile

        # 根据智能体类型分发
        if agent_type == "governance":
            return await self._handle_governance_agent(message)
        elif agent_type == "policy":
            return await self._handle_policy_agent(message, user_profile)
        elif agent_type == "health_profile":
            return await self._handle_health_agent(message, user_id, user_profile)
        elif agent_type == "coverage":
            return await self._handle_coverage_agent(message, user_profile, extra_context)
        elif agent_type == "eeg":
            return await self._handle_eeg_agent(message, user_id, user_profile)
        elif agent_type == "imaging":
            return await self._handle_imaging_agent(message, user_id, user_profile)
        elif agent_type == "body":
            return await self._handle_body_agent(message, user_id, user_profile, extra_context=extra_context)
        elif agent_type == "data":
            return await self._handle_data_agent(message, user_id, user_profile)
        elif agent_type == "drug":
            return await self._handle_drug_agent(message, user_id, user_profile)
        elif agent_type == "general":
            return await self._handle_general_agent(message, user_profile)
        else:
            # claims / security 等暂时使用 LLM 或 mock
            return await self._handle_generic_agent(agent_type, message, user_profile, extra_context)

    async def _handle_governance_agent(self, message: str) -> dict[str, Any]:
        """数据治理官：病历脱敏 + 结构化（调用 governance 服务，本地大模型优先）。"""
        import anyio

        from app.services.governance import govern

        # 从消息中提取病历正文：去掉指令性短语后，剩余足够长则视为病历原文
        note = message
        for w in ("帮我", "请", "治理", "脱敏", "结构化", "病历质控", "一下", "这段", "处理"):
            note = note.replace(w, "")
        note = note.strip(" ：:，,。\n")
        is_note = ("患者" in note and "诊断" in note) or len(note) >= 60

        if not is_note:
            return {
                "response": (
                    "我是数据治理官 🛡️ 请把**非结构化入院记录**发给我，我会：\n"
                    "1️⃣ 自动脱敏 PHI（身份证/手机号/姓名/住院号）\n"
                    "2️⃣ 用本地大模型结构化为标准数据集（全程院内网，数据不出院）\n\n"
                    "示例：直接粘贴一段以「患者某某，X岁……」开头的病历，或在「AI病历治理」页使用完整流水线。"
                ),
                "data": {"agent_type": "governance", "mode": "guide"},
            }

        result = await anyio.to_thread.run_sync(lambda: govern(note, use_llm=True))
        structured = result["structured"]
        extractor = structured.get("extractor", "")
        engine = "本地大模型 qwen3:4b" if extractor.startswith("llm") else "规则引擎（兜底）"
        dx = "、".join(structured.get("diagnoses") or []) or "—"
        meds = "、".join(
            f"{m.get('name')}{m.get('dose') or ''}" for m in (structured.get("medications") or [])
        ) or "—"
        response = (
            f"✅ 治理完成（{engine}）\n\n"
            f"🔒 脱敏：识别并掩码 {result['deid']['entity_count']} 处敏感实体\n"
            f"📋 诊断：{dx}\n"
            f"💊 用药：{meds}\n\n"
            f"治理产物已满足进入数据要素市场的合规要求，完整明细见「AI病历治理」页。"
        )
        return {
            "response": response,
            "data": {
                "agent_type": "governance",
                "mode": "executed",
                "entity_count": result["deid"]["entity_count"],
                "structured": structured,
            },
        }

    async def _handle_general_agent(
        self, message: str, user_profile: dict | None = None
    ) -> dict[str, Any]:
        """处理问候、身份询问和未命中专业意图的自然对话。"""
        name = (user_profile or {}).get("name") or "您"
        chronic = (user_profile or {}).get("chronic_diseases") or []

        if self._llm is not None:
            try:
                profile_text = ""
                if user_profile and user_profile.get("found"):
                    profile_text = (
                        f"当前用户：{name}，{user_profile.get('age', '')}岁，"
                        f"{user_profile.get('insurance_type', '')}，"
                        f"已记录健康关注：{'、'.join(chronic) if chronic else '暂无'}。"
                    )
                answer = await self._llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是 MedSignal 智能助手。自然、简洁地回应普通对话；"
                                "可引导用户使用医保权益、健康画像、报销预审、政策匹配、"
                                "脑电、影像和数字人体档案功能。不得虚构诊断或政策结论。"
                                + profile_text
                            ),
                        },
                        {"role": "user", "content": message},
                    ],
                    temperature=0.6,
                )
                return {"response": answer, "data": {"mode": "llm_general"}}
            except Exception as exc:
                logger.warning("通用对话 LLM 失败，使用离线回答: %s", exc)

        text = message.strip().lower()
        if any(word in text for word in ("你是谁", "能做什么", "怎么用", "帮助")):
            response = (
                f"您好，{name}！我是 MedSignal 医疗健康与医保智能助手。"
                "我可以结合当前用户资料，查询医保权益、评估健康风险、整理数字人体档案、"
                "辅助报销预审和政策匹配，也能解读脑电与医学影像演示结果。"
                "您可以直接问：‘我最近身体怎么样？’或‘我的住院费用大概能报多少？’"
            )
        elif any(word in text for word in ("我是谁", "我的资料", "介绍一下我")):
            if user_profile and user_profile.get("found"):
                response = (
                    f"当前选择的是{name}，{user_profile.get('age', '未知')}岁，"
                    f"参保类型为{user_profile.get('insurance_type', '未记录')}。"
                    f"档案中关注的健康问题包括：{'、'.join(chronic) if chronic else '暂无明确慢病记录'}。"
                    "您可以继续让我查看健康画像、就诊记录或数字人体档案。"
                )
            else:
                response = "当前没有找到对应用户资料，请先从右上角选择或新增用户。"
        elif any(word in text for word in ("你好", "您好", "嗨", "在吗")):
            response = f"您好，{name}！我在。您想先了解健康情况、医保权益，还是查看数字人体档案？"
        elif "谢谢" in text:
            response = "不客气。如果还想继续查看健康、医保或档案信息，直接告诉我即可。"
        else:
            response = (
                f"我明白您的问题。当前处于离线演示模式，暂时无法进行开放式大模型生成。"
                f"我仍可基于{name}的系统数据处理医保权益、健康画像、报销、政策、脑电、"
                "医学影像和数字人体档案相关问题；请补充您希望查询的具体方向。"
            )
        return {"response": response, "data": {"mode": "offline_general"}}

    async def _handle_policy_agent(self, message: str, user_profile: dict | None = None) -> dict[str, Any]:
        """处理政策查询智能体

        使用 KnowledgeBase 检索相关政策，再用 LLM 生成回答。
        若提供 user_profile，结合用户慢病/参保类型做个性化匹配。
        """
        # 0. 个性化查询改写：把用户慢病加入检索 query
        search_query = message
        if user_profile and user_profile.get("found"):
            chronic = user_profile.get("chronic_diseases") or []
            ins_type = user_profile.get("insurance_type", "")
            if chronic:
                search_query = f"{message}（用户情况：{ins_type}，慢病：{'、'.join(chronic)}）"

        # 1. 知识库检索（含嵌入生成，首次可能触发模型下载；超时/失败降级为无 RAG 上下文）
        search_results: list[SearchResult] = []
        if self._kb is not None:
            try:
                search_results = await asyncio.wait_for(
                    self._kb.search(search_query, top_k=5, min_score=0.3),
                    timeout=10.0,
                )
                logger.info("知识库检索到 %d 条相关结果", len(search_results))
            except TimeoutError:
                logger.warning("知识库检索超时(10s)，本次降级为无 RAG 上下文")
            except Exception as e:
                logger.error("知识库检索失败: %s", e)

        # 2. 如果有检索结果，使用 RAG 生成回答
        if search_results and self._llm is not None:
            try:
                # 构建上下文
                context = [
                    f"来源: {r.source} | 标题: {r.title}\n{r.content}"
                    for r in search_results
                ]

                # 个性化系统提示
                sys_prompt = "你是MedSignal的政策参谋。请基于政策资料准确回答，结合用户实际情况给出可享受的政策建议，并引用来源。"
                if user_profile and user_profile.get("found"):
                    sys_prompt += (
                        f"\n\n## 用户情况：{user_profile.get('name', '')}，"
                        f"{user_profile.get('age', '')}岁，{user_profile.get('insurance_type', '')}，"
                        f"慢病：{'、'.join(user_profile.get('chronic_diseases', []) or ['无'])}。"
                        "请优先匹配该用户能享受的政策。"
                    )

                # 使用 RAG 生成回答
                answer = await self._llm.chat_with_rag(
                    system_prompt=sys_prompt,
                    user_message=message,
                    context=context,
                )

                # 构建来源引用
                sources = list({
                    f"{r.title}（{r.source}）" for r in search_results[:3]
                })

                return {
                    "response": answer,
                    "data": {
                        "matched_count": len(search_results),
                        "sources": sources,
                        "top_policy": search_results[0].title if search_results else "",
                        "scores": [round(r.score, 4) for r in search_results[:3]],
                    },
                    "evidence": [
                        {"type": "policy_source", "title": r.title, "source": r.source, "score": round(r.score, 4)}
                        for r in search_results[:3]
                    ],
                }
            except Exception as e:
                logger.error("RAG 生成失败: %s，降级到检索结果拼接", e)

        # 3. 降级：直接返回检索结果（无 LLM）
        if search_results:
            # 拼接检索结果作为回答
            answer_parts = []
            for i, r in enumerate(search_results[:3], 1):
                answer_parts.append(f"**{i}. {r.title}**（来源: {r.source}）\n{r.content[:300]}")

            return {
                "response": f"为您匹配到 {len(search_results)} 条相关政策：\n\n" + "\n\n".join(answer_parts),
                "data": {
                    "matched_count": len(search_results),
                    "top_policy": search_results[0].title,
                    "sources": [f"{r.title}（{r.source}）" for r in search_results[:3]],
                },
            }

        # 4. 最终降级：mock 数据
        logger.warning("政策查询降级到 mock 数据")
        return self.MOCK_RESPONSES.get("policy", {"response": "暂无法处理该请求", "data": {}})

    async def _handle_health_agent(self, message: str, user_id: str | None = None,
                                   user_profile: dict | None = None) -> dict[str, Any]:
        """处理健康画像智能体

        优先使用数据库真实用户画像，配合 LLM 生成个性化健康预警和建议。
        """
        # 优先用注入的真实画像，否则用兜底假数据
        if user_profile and user_profile.get("found"):
            user_data = {
                "user_id": user_id,
                "name": user_profile.get("name", ""),
                "age": user_profile.get("age", 55),
                "chronic_diseases": user_profile.get("chronic_diseases", []),
                "medications": user_profile.get("medications", []),
                "medication_categories": user_profile.get("medication_categories", []),
                "recent_visits": user_profile.get("recent_visits", 0),
                "visit_count_6m": user_profile.get("visit_count_6m", 0),
                "diagnoses": user_profile.get("diagnoses", []),
                "medication_count": len(user_profile.get("medications", [])),
            }
        else:
            user_data = {
                "user_id": user_id,
                "age": 55,
                "chronic_diseases": ["高血压"],
                "recent_visits": 3,
                "medication_count": 2,
            }

        if self._llm is not None:
            try:
                alert_result = await self._llm.generate_health_alert(user_data)

                # 生成健康建议回答
                risk_level = alert_result.get("risk_level", "low")
                risk_label = {"low": "低风险", "medium": "中风险", "high": "高风险"}.get(risk_level, "未知")

                alerts_text = []
                for alert in alert_result.get("alerts", []):
                    alerts_text.append(
                        f"- **{alert.get('type', '')}**: {alert.get('description', '')}\n"
                        f"  建议: {alert.get('suggestion', '')}\n"
                        f"  政策提示: {alert.get('related_policy', '')}"
                    )

                response = f"您的健康风险评估：**{risk_label}**\n\n"
                if alerts_text:
                    response += "⚠️ 预警信息：\n" + "\n".join(alerts_text)
                else:
                    response += "暂无明显风险，请继续保持健康生活方式。"

                return {
                    "response": response,
                    "data": {
                        "health_score": 70 if risk_level == "low" else (50 if risk_level == "medium" else 30),
                        "risk_level": risk_level,
                        "chronic_diseases": user_data.get("chronic_diseases", []),
                        "alert_count": len(alert_result.get("alerts", [])),
                    },
                }
            except Exception as e:
                logger.error("健康预警生成失败: %s，降级到 mock 数据", e)

        # 降级：mock 数据
        return self.MOCK_RESPONSES.get("health_profile", {"response": "暂无法处理该请求", "data": {}})

    async def _handle_coverage_agent(self, message: str, user_profile: dict | None = None,
                                     extra_context: str = "") -> dict[str, Any]:
        """处理报销待遇智能体

        尝试从知识库检索相关报销政策，再用 LLM 生成回答。
        若提供 user_profile，结合参保类型做个性化回答。
        """
        search_query = message
        if user_profile and user_profile.get("found"):
            ins_type = user_profile.get("insurance_type", "")
            emp = user_profile.get("employee_status", "")
            search_query = f"{message}（用户：{ins_type}/{emp}）"

        # 尝试从知识库获取报销相关信息（超时/失败降级为无 RAG 上下文，防嵌入模型拖慢请求）
        if self._kb is not None:
            try:
                search_results = await asyncio.wait_for(
                    self._kb.search(search_query, top_k=3, category="职工医保/居民医保基本政策", min_score=0.3),
                    timeout=10.0,
                )
                if not search_results:
                    search_results = await asyncio.wait_for(
                        self._kb.search(search_query, top_k=3, min_score=0.3),
                        timeout=10.0,
                    )

                if search_results and self._llm is not None:
                    context = [
                        f"来源: {r.source} | 标题: {r.title}\n{r.content}"
                        for r in search_results
                    ]
                    sys_prompt = "你是MedSignal的权益管家，请根据政策资料准确回答用户的报销比例、起付线、封顶线、个人账户等问题。"
                    if user_profile and user_profile.get("found"):
                        sys_prompt += (
                            f"\n\n## 用户情况：{user_profile.get('name', '')}，"
                            f"{user_profile.get('insurance_type', '')}，{user_profile.get('employee_status', '')}。"
                            "请给出该用户适用的具体待遇数据。"
                        )
                    answer = await self._llm.chat_with_rag(
                        system_prompt=sys_prompt,
                        user_message=f"{extra_context}\n\n用户问题：{message}" if extra_context else message,
                        context=context,
                    )
                    return {
                        "response": answer,
                        "data": {
                            "matched_count": len(search_results),
                            "sources": [f"{r.title}（{r.source}）" for r in search_results[:3]],
                        },
                        "evidence": [
                            {"type": "policy_source", "title": r.title, "source": r.source}
                            for r in search_results[:3]
                        ],
                    }

                if search_results:
                    return {
                        "response": f"根据政策资料：{search_results[0].content[:300]}",
                        "data": {
                            "matched_count": len(search_results),
                            "top_policy": search_results[0].title,
                        },
                    }
            except Exception as e:
                logger.error("报销查询失败: %s", e)

        # 降级：mock 数据
        return self.MOCK_RESPONSES.get("coverage", {"response": "暂无法处理该请求", "data": {}})

    async def _handle_eeg_agent(
        self, message: str, user_id: str | None = None,
        user_profile: dict | None = None,
    ) -> dict[str, Any]:
        """处理脑电健康智能体（「脑电卫士」，关键医疗信号识别核心）

        流程：EEG 采集（合成信号）→ 频域特征提取 → 健康指标 → 异常预警 → 医保政策联动
        若有 LLM，进一步用自然语言解读脑电结果；否则用结构化模板回答。
        """
        from app.services.eeg import engine as eeg_engine

        # 根据用户画像推荐心理状态（Demo 时模拟采集场景）
        mental_state = eeg_engine.pick_mental_state_by_profile(user_profile)
        session = eeg_engine.assess_session(
            user_id=user_id or "1",
            mental_state=mental_state,
            duration_seconds=4,
            user_profile=user_profile,
            seed=42,
        )

        metrics = session.metrics
        alerts = session.alerts
        policy_links = session.policy_links

        # 结构化文本回答（无论是否有 LLM 都先构造，LLM 可在此基础上润色）
        name = (user_profile or {}).get("name", "您")
        stress = metrics.get("stress_index", 0)
        attention = metrics.get("attention_index", 0)
        sleep = metrics.get("sleep_quality", 0)
        cognitive = metrics.get("cognitive_load", 0)
        emotion = metrics.get("emotion", {})
        emotion_label = emotion.get("label", "平稳")

        parts = [
            f"**脑电健康评估完成**（{name}，心理状态：{session.mental_state_label}）\n",
            f"- 压力指数：{stress}/100",
            f"- 注意力指数：{attention}/100",
            f"- 睡眠质量：{sleep}/100",
            f"- 认知负荷：{cognitive}/100",
            f"- 情绪状态：{emotion_label}\n",
        ]

        if alerts:
            parts.append(f"⚠️ 检测到 {len(alerts)} 项脑电健康预警：")
            for a in alerts[:3]:
                parts.append(f"- **{a.get('title', '')}**：{a.get('description', '')}")
                if a.get("suggestion"):
                    parts.append(f"  建议：{a.get('suggestion')}")
        else:
            parts.append("✅ 脑电指标正常，未发现异常预警。")

        if policy_links:
            parts.append(f"\n💡 已为您匹配 {len(policy_links)} 项相关医保政策：")
            for p in policy_links[:3]:
                parts.append(f"- **{p.get('policy_hint', '')}**：{p.get('suggestion', '')}")

        structured_response = "\n".join(parts)

        # 若 LLM 可用，用 LLM 润色为更自然的回答
        if self._llm is not None:
            try:
                sys_prompt = (
                    "你是MedSignal的脑电卫士智能体（EEG Agent），负责解读 EEG 脑电评估结果并给出健康建议。"
                    "基于五频段功率（δ/θ/α/β/γ）和四维健康指标（压力/注意力/睡眠/认知负荷）解读用户脑电状态，"
                    "并主动推荐相关医保政策。回答要专业、温暖、可操作，体现'脑电采集→健康评估→医保联动'全链路。"
                )
                if user_profile and user_profile.get("found"):
                    sys_prompt += (
                        f"\n\n## 用户情况：{user_profile.get('name', '')}，"
                        f"{user_profile.get('age', '')}岁，{user_profile.get('insurance_type', '')}，"
                        f"慢病：{'、'.join(user_profile.get('chronic_diseases', []) or ['无'])}。"
                    )
                user_msg = (
                    f"用户问题：{message}\n\n"
                    f"脑电评估结果（结构化数据）：\n{structured_response}\n\n"
                    f"频段功率：{session.avg_band_powers}\n"
                    f"健康指标：{metrics}\n"
                    f"联动政策：{policy_links}"
                )
                answer = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.5,
                )
                return {
                    "response": answer,
                    "data": {
                        "mental_state": session.mental_state,
                        "mental_state_label": session.mental_state_label,
                        "metrics": metrics,
                        "alert_count": len(alerts),
                        "policy_link_count": len(policy_links),
                        "session_id": session.session_id,
                    },
                    "evidence": [
                        {"type": "eeg_metric", "metric": k, "value": v}
                        for k, v in metrics.items() if isinstance(v, (int, float))
                    ] + [
                        {"type": "eeg_policy_link", "policy": p.get("policy_hint")}
                        for p in policy_links[:3]
                    ],
                }
            except Exception as e:
                logger.error("EEG Agent LLM 解读失败: %s，降级结构化回答", e)

        # 降级：直接返回结构化回答
        return {
            "response": structured_response,
            "data": {
                "mental_state": session.mental_state,
                "mental_state_label": session.mental_state_label,
                "metrics": metrics,
                "alert_count": len(alerts),
                "policy_link_count": len(policy_links),
                "session_id": session.session_id,
            },
            "evidence": [
                {"type": "eeg_metric", "metric": k, "value": v}
                for k, v in metrics.items() if isinstance(v, (int, float))
            ] + [
                {"type": "eeg_policy_link", "policy": p.get("policy_hint")}
                for p in policy_links[:3]
            ],
        }

    async def _handle_imaging_agent(
        self, message: str, user_id: str | None = None,
        user_profile: dict | None = None,
    ) -> dict[str, Any]:
        """处理医学影像智能体（第 7 个智能体「影像卫士」，多模态核心创新）

        流程：AI 影像分析（合成影像）→ 病灶检测 → 预标注 → 医生复核建议 → 医保联动
        若用户提到具体检查类型（胸片/CT/MRI），按类型分析；否则默认胸片。
        """
        from app.services.imaging import engine as imaging_engine

        # 从消息中识别检查类型
        study_type = "chest_xray"
        if any(k in message for k in ["CT", "ct", "肺CT", "肺部CT", "CT扫描"]):
            study_type = "lung_ct"
        elif any(k in message for k in ["核磁", "MRI", "mri", "脑MRI", "脑部"]):
            study_type = "brain_mri"
        elif any(k in message for k in ["胸片", "X光", "X 光", "X-ray", "x-ray", "胸部"]):
            study_type = "chest_xray"

        study = imaging_engine.generate_study(
            study_type=study_type,
            findings_keys=None,
            seed=42,
        )
        findings = study.findings
        policy_links = imaging_engine.link_to_imaging_policies(findings)

        # 结构化文本回答
        study_label = imaging_engine.STUDY_TYPES[study_type]["label"]
        parts = [
            f"**医学影像 AI 分析完成**（{study_label}）\n",
            f"AI 引擎共检出 {len(findings)} 处疑似征象：",
        ]
        for i, f in enumerate(findings[:5], 1):
            label = imaging_engine.FINDINGS_META.get(f.finding_type, {}).get("label", f.finding_type)
            parts.append(
                f"{i}. **{label}**（置信度 {f.confidence:.0%}，严重度：{f.severity}）"
                f"位于影像 {f.x:.2f},{f.y:.2f} 处"
            )

        parts.append("")
        parts.append("⚠️ 以上为 AI 预标注，仅供筛查参考，**须由持证医师复核确认**后再出具诊断意见。")

        if policy_links:
            parts.append(f"\n💡 已为您匹配 {len(policy_links)} 项相关医保政策：")
            for p in policy_links[:3]:
                parts.append(f"- **{p.get('policy_hint', '')}**：{p.get('suggestion', '')}")

        structured_response = "\n".join(parts)

        # 若 LLM 可用，用 LLM 润色为更自然的回答
        if self._llm is not None:
            try:
                sys_prompt = (
                    "你是 MedSignal Agent 的影像卫士智能体（Imaging Agent），负责解读医学影像 AI 分析结果。"
                    "说明 AI 检测到的病灶征象、置信度与严重度，强调 AI 标注需医师复核，"
                    "并主动推荐相关医保检查报销政策。回答要专业、严谨、可操作，"
                    "体现'影像分析→病灶识别→医师复核→医保联动'全链路，同时强调医疗安全边界。"
                )
                user_msg = (
                    f"用户问题：{message}\n\n"
                    f"影像分析结果（结构化数据）：\n{structured_response}\n\n"
                    f"发现详情：{[f.to_dict() for f in findings]}\n"
                    f"联动政策：{policy_links}"
                )
                answer = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.4,
                )
                return {
                    "response": answer,
                    "data": {
                        "study_type": study_type,
                        "study_label": study_label,
                        "finding_count": len(findings),
                        "policy_link_count": len(policy_links),
                        "study_id": study.study_id,
                    },
                    "evidence": [
                        {"type": "imaging_finding", "finding": f.finding_type, "confidence": f.confidence}
                        for f in findings[:5]
                    ] + [
                        {"type": "imaging_policy_link", "policy": p.get("policy_hint")}
                        for p in policy_links[:3]
                    ],
                }
            except Exception as e:
                logger.error("Imaging Agent LLM 解读失败: %s，降级结构化回答", e)

        return {
            "response": structured_response,
            "data": {
                "study_type": study_type,
                "study_label": study_label,
                "finding_count": len(findings),
                "policy_link_count": len(policy_links),
                "study_id": study.study_id,
            },
            "evidence": [
                {"type": "imaging_finding", "finding": f.finding_type, "confidence": f.confidence}
                for f in findings[:5]
            ] + [
                {"type": "imaging_policy_link", "policy": p.get("policy_hint")}
                for p in policy_links[:3]
            ],
        }

    async def _handle_generic_agent(self, agent_type: str, message: str,
                                    user_profile: dict | None = None,
                                    extra_context: str = "") -> dict[str, Any]:
        """处理通用智能体（claims / security 等）

        优先使用专业 Agent 提示词 + LLM 对话，不可用时降级到 mock 数据。
        """
        if self._llm is not None:
            try:
                # 优先使用 prompts/agent_prompts.py 里的专业系统提示词
                agent_descriptions = {
                    "claims": "你是MedSignal的报销助手，帮助用户了解报销流程、准备报销材料、解读报销差额。回答要专业、具体、可操作。",
                    "security": "你是MedSignal的安全守门，解答用户关于数据授权、隐私保护、审计追溯、可信数据空间的问题。强调'数据可用不可见'理念。",
                }
                system_prompt = agent_descriptions.get(agent_type, "你是MedSignal的智能助手，请专业、准确地回答用户问题。")

                # 注入用户上下文
                user_msg = message
                if user_profile and user_profile.get("found"):
                    user_msg = (
                        f"[用户上下文：{user_profile.get('name', '')}，{user_profile.get('age', '')}岁，"
                        f"{user_profile.get('insurance_type', '')}，慢病：{'、'.join(user_profile.get('chronic_diseases', []) or ['无'])}]\n\n"
                        f"用户问题：{message}"
                    )
                if extra_context:
                    user_msg = f"{extra_context}\n\n{user_msg}"

                answer = await self._llm.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.5,
                )
                return {
                    "response": answer,
                    "data": {"agent_type": agent_type},
                }
            except Exception as e:
                logger.error("LLM 对话失败: %s，降级到 mock 数据", e)

        # 降级：mock 数据
        return self.MOCK_RESPONSES.get(agent_type, {"response": "暂无法处理该请求", "data": {}})

    # ------------------------------------------------------------------
    # 档案管家（Body Agent）— 第 8 个智能体：人体健康档案
    #
    # 规划（LLM → 规则）→ 固定工具（归档/检索/对比/追问/交接）→ 组织回答（结构化 → LLM 润色）
    # 只整理用户提供的信息，不做任何诊断或推断；档案只增不删，是智能体的跨会话记忆。
    # ------------------------------------------------------------------

    BODY_TOOLS = ("archive", "retrieve", "compare", "ask_missing", "handoff")
    _BODY_QUERY_WORDS = ("看看", "查看", "有哪些", "上次", "之前", "怎么样", "查一下", "列出",
                         "档案里", "有没有记录", "历史记录")
    _BODY_COMPARE_WORDS = ("对比", "变化", "趋势", "前后", "两次", "比较")
    _BODY_AGENT_NAMES = {"policy": "政策参谋", "coverage": "权益管家"}

    def _rule_body_plan(self, message: str, summary: dict) -> dict:
        """规则规划（离线/LLM 失败时）：对比 > 查看 > 归档；报销/权益类问题追加交接。"""
        organs = body_taxonomy.match_organs(message)
        focus = organs[0] if organs else None
        actions: list[dict] = []
        if any(w in message for w in self._BODY_COMPARE_WORDS):
            actions.append({"tool": "compare", "args": {"organ": focus}})
        elif any(w in message for w in self._BODY_QUERY_WORDS):
            actions.append({"tool": "retrieve", "args": {"organ": focus}})
        elif body_taxonomy.has_health_signal(message):
            actions.append({"tool": "archive", "args": {}})
        else:
            actions.append({"tool": "retrieve", "args": {"organ": None}})
        if any(w in message for w in ("报多少", "权益", "账户", "余额", "缴费", "报销比例")):
            actions.append({"tool": "handoff", "args": {"agent": "coverage"}})
        elif any(w in message for w in ("报销", "政策", "能报", "待遇", "省钱")):
            actions.append({"tool": "handoff", "args": {"agent": "policy"}})
        return {"actions": actions, "focus": focus}

    async def _plan_body_actions(self, message: str, summary: dict) -> dict:
        """LLM 规划工具序列（10s 超时），失败降级规则规划。"""
        if self._llm is not None:
            try:
                sys_prompt = (
                    "你是 MedSignal 档案管家的规划模块。根据用户消息和档案概况，决定要执行的工具序列，只返回 JSON。\n"
                    "可用工具：archive（用户陈述了新的身体部位/症状/检查信息时归档）、"
                    "retrieve（用户想查看档案，args.organ 可为 null 表示全部）、"
                    "compare（用户想对比同一部位不同时间的记录，args.organ）、"
                    "ask_missing（需要用户补充信息，args.questions 列表）、"
                    "handoff（用户问报销/政策/权益，args.agent ∈ policy|coverage）。\n"
                    f"organ 只能取：{', '.join(body_taxonomy.ORGANS)}\n"
                    '格式：{"actions":[{"tool":"archive","args":{}}],"focus":"lungs 或 null"}'
                )
                resp = await asyncio.wait_for(
                    self._llm.chat(
                        [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": f"档案概况：{summary}\n用户消息：{message}"},
                        ],
                        temperature=0.1,
                    ),
                    timeout=10.0,
                )
                plan = LLMService._parse_json_response(resp)
                if isinstance(plan, dict) and isinstance(plan.get("actions"), list):
                    actions = [a for a in plan["actions"]
                               if isinstance(a, dict) and a.get("tool") in self.BODY_TOOLS]
                    if actions:
                        focus = plan.get("focus")
                        return {"actions": actions, "focus": focus if focus in body_taxonomy.ORGANS else None}
            except Exception as e:
                logger.warning("档案管家规划失败: %s，降级规则规划", e)
        return self._rule_body_plan(message, summary)

    async def _run_body_tools(
        self, db, plan: dict, message: str, user_id: str, user_profile: dict | None, *,
        source_type: str, source_label: str, source_ref: str = "",
        document_id: int | None = None, text: str | None = None,
    ) -> dict:
        """按规划顺序执行工具；每个工具失败只记日志不中断。text 为待归档原文（默认为 message）。"""
        from app import crud
        from app.services.body import extractor

        state: dict[str, Any] = {
            "archived": [], "records": [], "queried": None, "comparison": None,
            "missing_info": [], "handoff": [], "handoff_results": {}, "focus": plan.get("focus"),
        }
        text = message if text is None else text

        for action in plan.get("actions", []):
            tool = action.get("tool")
            args = action.get("args") if isinstance(action.get("args"), dict) else {}
            try:
                if tool == "archive":
                    new = await extractor.ingest_text(
                        db, user_id, text, source_type=source_type, source_label=source_label,
                        source_ref=source_ref, llm=self._llm, document_id=document_id,
                    )
                    state["archived"].extend(new)
                    state["missing_info"].extend(extractor.missing_info(new))
                    if new and not state["focus"]:
                        state["focus"] = new[0]["organ"]
                elif tool == "retrieve":
                    organ = args.get("organ") if args.get("organ") in body_taxonomy.ORGANS else None
                    rows = await crud.get_body_records(db, user_id, organ=organ, limit=20)
                    state["records"] = [crud.body_record_to_dict(r) for r in rows]
                    state["queried"] = organ or "__all__"
                    if organ and not state["focus"]:
                        state["focus"] = organ
                elif tool == "compare":
                    organ = args.get("organ") or state["focus"]
                    if organ not in body_taxonomy.ORGANS:
                        summary = await crud.get_body_organ_summary(db, user_id)
                        organ = max(summary, key=lambda k: summary[k]["count"]) if summary else None
                    if organ:
                        rows = await crud.get_body_records(db, user_id, organ=organ, limit=2)
                        dicts = [crud.body_record_to_dict(r) for r in rows]
                        if len(dicts) >= 2:
                            state["comparison"] = {
                                "organ": organ, "organ_label": body_taxonomy.label_of(organ),
                                "later": dicts[0], "earlier": dicts[1],
                            }
                        else:
                            state["records"], state["queried"] = dicts, organ
                        state["focus"] = state["focus"] or organ
                    else:
                        state["queried"] = "__all__"
                elif tool == "ask_missing":
                    state["missing_info"].extend(str(q) for q in (args.get("questions") or []) if q)
                elif tool == "handoff":
                    target = args.get("agent")
                    if target in self._BODY_AGENT_NAMES and target not in state["handoff"]:
                        state["handoff"].append(target)
            except Exception as e:
                logger.error("档案管家工具 %s 执行失败: %s", tool, e)
        state["missing_info"] = list(dict.fromkeys(state["missing_info"]))

        # 交接：用刷新后的画像（已含本次归档）调用现有专业智能体
        if state["handoff"]:
            profile = user_profile
            if state["archived"] or not profile:
                try:
                    profile = await crud.get_user_health_profile(db, user_id)
                except Exception as e:
                    logger.warning("刷新用户画像失败: %s", e)
            for target in state["handoff"]:
                try:
                    handler = (self._handle_policy_agent(message, profile) if target == "policy"
                               else self._handle_coverage_agent(message, profile))
                    r = await asyncio.wait_for(handler, timeout=15.0)
                    state["handoff_results"][target] = (r or {}).get("response", "")
                except Exception as e:
                    logger.warning("档案管家交接 %s 失败: %s", target, e)
        return state

    def _compose_body_response(self, state: dict, intro: str | None = None) -> str:
        """结构化回答：已记录 → 并列对比 → 档案列表 → 交接结果 → 追问 → 固定免责。"""
        from app.services.body import extractor

        parts: list[str] = [intro] if intro else []
        if state["archived"]:
            parts.append(f"✅ 已为您记录 {len(state['archived'])} 条信息到健康档案：")
            parts.append(extractor.records_to_text(state["archived"]))
        if state.get("link_note"):
            parts.append(state["link_note"])
        c = state.get("comparison")
        if c:
            parts.append(f"📋 {c['organ_label']}最近两条记录并列对比（仅并列原文，是否变化请以医生判断为准）：")
            for tag, r in (("较早", c["earlier"]), ("较新", c["later"])):
                parts.append(
                    f"- {tag} [{r.get('event_date') or '日期未注明'}][{r.get('source_label', '')}]："
                    f"“{r.get('raw_excerpt') or r.get('description', '')}”"
                )
        queried = state.get("queried")
        if state["records"]:
            where = f"{body_taxonomy.label_of(queried)}的" if queried and queried != "__all__" else ""
            parts.append(f"📁 档案中{where}记录（共 {len(state['records'])} 条，按时间倒序）：")
            parts.append(extractor.records_to_text(state["records"]))
        elif queried:
            parts.append("📁 您的健康档案暂无记录。" if queried == "__all__"
                         else f"📁 {body_taxonomy.label_of(queried)}暂无相关医疗记录。")
        for target, text in state["handoff_results"].items():
            if text:
                parts.append(f"\n**【{self._BODY_AGENT_NAMES[target]}】**\n{text[:600]}")
        if state["missing_info"]:
            parts.append("❓ 为了档案更准确，请补充：" + "；".join(state["missing_info"][:2]))
        if not (state["archived"] or state["records"] or c or queried or state["handoff_results"]):
            parts.append("您可以直接告诉我身体某个部位的情况（如“2026年2月查出肺结节”），"
                         "或上传 CT/MRI 报告，我会按部位整理归档，随时可查看、对比。")
        parts.append(f"\nℹ️ {extractor.DISCLAIMER}")
        return "\n".join(parts)

    async def _polish_body_response(self, message: str, structured: str, extra_context: str = "") -> str:
        """LLM 润色（15s 超时）；严格限定不得新增档案外信息、不得诊断。失败返回结构化回答。"""
        if self._llm is None:
            return structured
        try:
            from app.prompts.agent_prompts import BODY_AGENT_PROMPT

            sys_prompt = (
                BODY_AGENT_PROMPT
                + "\n\n现在请用自然、简洁的中文直接回复用户（不要输出 JSON）。"
                  "逐条保留 [日期][来源] 标签和原文引用；不得添加档案里没有的信息；"
                  "不得做任何诊断、判断或医疗建议；保留末尾免责声明。"
            )
            answer = await asyncio.wait_for(
                self._llm.chat(
                    [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": (
                            f"{extra_context}\n\n" if extra_context else ""
                        ) + f"用户消息：{message}\n\n档案管家执行结果：\n{structured}"},
                    ],
                    temperature=0.3,
                ),
                timeout=15.0,
            )
            if answer and "暂不可用" not in answer and "出现问题" not in answer:
                return answer
        except Exception as e:
            logger.warning("档案管家润色失败: %s，返回结构化回答", e)
        return structured

    @staticmethod
    def _body_data(state: dict) -> dict:
        return {
            "body_updates": state["archived"],
            "body_focus": state.get("focus"),
            "records": state["records"],
            "comparison": state.get("comparison"),
            "missing_info": state["missing_info"],
            "handoff": state["handoff"],
            "organ_summary": state.get("summary", {}),
        }

    @staticmethod
    def _body_evidence(state: dict) -> list[dict]:
        items = list(state["archived"]) + list(state["records"][:5])
        if state.get("comparison"):
            items += [state["comparison"]["earlier"], state["comparison"]["later"]]
        seen, out = set(), []
        for r in items:
            key = r.get("id") or (r.get("organ"), r.get("event_date"), r.get("raw_excerpt"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "type": "body_record", "organ": r.get("organ"), "organ_label": r.get("organ_label"),
                "event_date": r.get("event_date"), "source_label": r.get("source_label"),
                "excerpt": (r.get("raw_excerpt") or r.get("description") or "")[:80],
            })
        return out

    async def _handle_body_agent(
        self, message: str, user_id: str | None = None,
        user_profile: dict | None = None, db=None, extra_context: str = "",
    ) -> dict[str, Any]:
        """档案管家：规划 → 工具（归档/检索/对比/追问/交接）→ 组织回答。只整理，不诊断。

        db 可注入（测试用）；默认自行开 session。
        """
        from app import crud
        from app.database import async_session
        from app.services.body import extractor

        uid = user_id or "user_001"

        async def _run(session) -> dict:
            summary = await crud.get_body_organ_summary(session, uid)
            plan = await self._plan_body_actions(message, summary)
            logger.info("档案管家规划: %s", plan)
            st = await self._run_body_tools(
                session, plan, message, uid, user_profile,
                source_type="chat", source_label=extractor.SOURCE_CHAT,
            )
            st["summary"] = await crud.get_body_organ_summary(session, uid)
            return st

        if db is not None:
            state = await _run(db)
        else:
            async with async_session() as session:
                state = await _run(session)

        structured = self._compose_body_response(state)
        response = await self._polish_body_response(message, structured, extra_context)
        return {"response": response, "data": self._body_data(state), "evidence": self._body_evidence(state)}

    async def handle_body_document(
        self, user_id: str, text: str, doc_kind: str, filename: str, mime: str = "",
        user_profile: dict | None = None, db=None,
    ) -> dict[str, Any]:
        """上传资料 = 档案管家事件：存档 → 归档 → 同部位并入时间线并列对比 → 涉及复查/费用时交接政策参谋。"""
        from app import crud
        from app.database import async_session
        from app.services.body import extractor

        uid = user_id or "user_001"
        handoff_msg = f"我上传了一份{doc_kind}《{filename}》，相关检查/复查费用有哪些医保报销政策？"

        async def _run(session) -> tuple:
            before = await crud.get_body_organ_summary(session, uid)
            doc = await crud.create_body_document(session, uid, filename, mime, doc_kind, text or "")
            actions = [{"tool": "archive", "args": {}}]
            if any(k in (text or "") for k in ("复查", "检查", "费用", "报销")):
                actions.append({"tool": "handoff", "args": {"agent": "policy"}})
            st = await self._run_body_tools(
                session, {"actions": actions, "focus": None}, handoff_msg, uid, user_profile,
                source_type="upload", source_label=doc_kind, source_ref=filename,
                document_id=doc.id, text=text or "",
            )
            # 同部位已有历史 → 并入时间线并列对比
            repeated = [o for o in dict.fromkeys(r["organ"] for r in st["archived"]) if o in before]
            if repeated:
                organ = repeated[0]
                rows = await crud.get_body_records(session, uid, organ=organ, limit=2)
                dicts = [crud.body_record_to_dict(r) for r in rows]
                if len(dicts) >= 2:
                    st["comparison"] = {
                        "organ": organ, "organ_label": body_taxonomy.label_of(organ),
                        "later": dicts[0], "earlier": dicts[1],
                    }
                    st["link_note"] = (
                        f"🔗 {body_taxonomy.label_of(organ)}此前已有 {before[organ]['count']} 条记录，"
                        "本次已并入同一时间线："
                    )
            st["summary"] = await crud.get_body_organ_summary(session, uid)
            return doc, st

        if db is not None:
            doc, state = await _run(db)
        else:
            async with async_session() as session:
                doc, state = await _run(session)

        intro = f"📄 已解析《{filename}》（{doc_kind}）"
        if not state["archived"]:
            intro += "，未识别到身体部位相关信息，资料已存档。"
            if not (text or "").strip():
                intro += "（未提取到文字：扫描版 PDF 没有文字层，可改传清晰图片。）"
        structured = self._compose_body_response(state, intro=intro)
        response = await self._polish_body_response(f"用户上传了{doc_kind}《{filename}》", structured)
        return {
            "document_id": doc.id,
            "doc_kind": doc_kind,
            "filename": filename,
            "records_added": len(state["archived"]),
            "records": state["archived"],
            "agent_response": response,
            "body_focus": state.get("focus"),
            "comparison": state.get("comparison"),
            "handoff": state["handoff"],
            "missing_info": state["missing_info"],
            "organ_summary": state["summary"],
            "disclaimer": extractor.DISCLAIMER,
        }

    # ------------------------------------------------------------------
    # 数据管家（湖仓一体数据智能体）
    # ------------------------------------------------------------------

    async def _handle_data_agent(
        self, message: str, user_id: str | None = None,
        user_profile: dict | None = None, db=None,
    ) -> dict[str, Any]:
        """数据管家：湖仓一体数据中枢。

        自然语言问题 → 预置模板/LLM NL2SQL → 只读执行 → 结构化回答（SQL 透明）。
        db 可注入（测试用）；默认自行开 session。
        """
        from app.database import async_session
        from app.services.data_lake import engine as data_engine

        async def _run(session) -> dict:
            return await data_engine.smart_query(session, message, llm=self._llm, user_id=user_id)

        if db is not None:
            result = await _run(db)
        else:
            async with async_session() as session:
                result = await _run(session)

        structured = data_engine.format_chat_response(result)
        response = await self._polish_data_response(message, structured)

        query = result.get("query") or {}
        evidence = []
        if query.get("sql"):
            evidence.append({
                "type": "data_query", "sql": query["sql"],
                "row_count": query.get("row_count", 0), "source": query.get("source"),
            })
        return {
            "response": response,
            "data": {
                "query": query,
                "result_table": result.get("result_table"),
                "data_source": result.get("data_source"),
                "catalog_summary": (result.get("catalog") or {}).get("summary"),
            },
            "evidence": evidence,
        }

    async def _polish_data_response(self, message: str, structured: str) -> str:
        """LLM 润色（15s 超时）；严禁编造查询结果之外的数字。失败返回结构化回答。"""
        if self._llm is None:
            return structured
        try:
            from app.prompts.agent_prompts import DATA_AGENT_PROMPT

            sys_prompt = (
                DATA_AGENT_PROMPT
                + "\n\n现在请用自然、简洁的中文直接回复用户（不要输出 JSON）。"
                  "所有数字必须且只能来自下方查询结果，不得估算或编造；"
                  "保留 SQL 与口径说明；用户转向报销/政策问题时提示可交接对应智能体。"
            )
            answer = await asyncio.wait_for(
                self._llm.chat(
                    [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": f"用户消息：{message}\n\n数据管家执行结果：\n{structured}"},
                    ],
                    temperature=0.3,
                ),
                timeout=15.0,
            )
            if answer and "暂不可用" not in answer and "出现问题" not in answer:
                return answer
        except Exception as e:
            logger.warning("数据管家润色失败: %s，返回结构化回答", e)
        return structured

    # ------------------------------------------------------------------
    # 药品卫士（拍照识别 × 用药安全）
    # ------------------------------------------------------------------

    async def _handle_drug_agent(
        self, message: str, user_id: str | None = None, user_profile: dict | None = None,
    ) -> dict[str, Any]:
        """药品卫士：处理聊天中的药品类文字问答，并引导拍照识别。

        聊天仅支持文字；药盒照片扫描走专用端点 POST /api/drugs/scan
        （识别后是否登记用药记录由用户确认）。
        """
        mock = self.MOCK_RESPONSES["drug"]
        if self._llm is None:
            return dict(mock)
        try:
            from app.prompts.agent_prompts import DRUG_AGENT_PROMPT

            profile_ctx = ""
            if user_profile and user_profile.get("found"):
                profile_ctx = (
                    f"\n\n## 用户情况\n姓名：{user_profile.get('name', '')}，"
                    f"年龄：{user_profile.get('age', '')}，"
                    f"慢病：{'、'.join(user_profile.get('chronic_diseases', []) or ['无'])}。"
                )
            sys_prompt = (
                DRUG_AGENT_PROMPT + profile_ctx
                + "\n\n当前为文字对话：若用户想识别具体药品，请引导其使用首页“药品识别”入口上传药盒照片。"
            )
            answer = await asyncio.wait_for(
                self._llm.chat(
                    [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": message},
                    ],
                    temperature=0.4,
                ),
                timeout=15.0,
            )
            if answer and answer.strip():
                return {"response": answer.strip(), "data": {"agent": "drug_agent"}}
        except Exception as e:
            logger.warning("药品卫士 LLM 回答失败，降级 mock: %s", e)
        return dict(mock)

    # ------------------------------------------------------------------
    # 结果聚合
    # ------------------------------------------------------------------

    def aggregate_results(self, result: dict[str, Any], agent_type: str = "") -> dict[str, Any]:
        """聚合智能体结果，补充建议"""
        suggestions_map: dict[str, list[str]] = {
            "coverage": [
                "查看您近12个月的缴费明细",
                "测算不同医院的报销差异",
                "了解门诊慢病待遇如何提高报销比例",
            ],
            "claims": [
                "上传发票让我帮您预审报销金额",
                "查看报销所需材料清单",
                "了解报销进度和到账情况",
            ],
            "governance": ["病历治理", "病历", "脱敏", "结构化", "PHI", "病历质控", "治理病历", "病历脱敏", "入院记录"],
        "health_profile": [
                "查看完整的健康画像雷达图",
                "了解近期用药安全提醒",
                "获取个性化健康改善建议",
            ],
            "policy": [
                "查看为您匹配的省钱政策清单",
                "了解门诊慢病认定申请流程",
                "查询异地就医备案操作",
            ],
            "security": [
                "管理智能体数据访问授权",
                "查看完整的审计日志",
                "了解您的数据权利",
            ],
            "eeg": [
                "发起一次脑电采集会话",
                "查看脑电健康趋势",
                "了解脑电异常对应的医保政策",
            ],
            "body": [
                "看看我的健康档案有哪些记录",
                "对比同一部位不同时间的记录",
                "上传 CT/MRI 报告，自动归档到对应部位",
            ],
            "general": [
                "查看我的健康画像",
                "查看我的医保权益",
                "打开数字人体档案",
            ],
            "data": [
                "帮我汇总就医费用",
                "看看我的用药 TOP10",
                "湖仓里有哪些数据资产",
            ],
            "drug": [
                "如何拍照识别药盒？",
                "怎么查看药品有效期和批准文号？",
                "把扫描到的药加入用药记录",
            ],
        }
        suggestions = suggestions_map.get(agent_type, [
            "您可以问我关于医保报销比例的问题",
            "需要理赔帮助？试试上传发票图片",
            "查看您的健康画像和风险预警",
        ])
        agent_type_label = {
            "coverage": "coverage_agent",
            "claims": "claims_agent",
            "health_profile": "health_agent",
            "policy": "policy_agent",
            "security": "security_agent",
            "eeg": "eeg_agent",
            "body": "body_agent",
            "general": "assistant_agent",
            "data": "data_agent",
            "drug": "drug_agent",
        }.get(agent_type, "orchestrator_agent")
        return {
            "agent_type": agent_type_label,
            "response": result.get("response", ""),
            "data": result.get("data", {}),
            "evidence": result.get("evidence"),
            "suggestions": suggestions,
        }
