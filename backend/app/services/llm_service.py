"""
MedSignal - LLM 服务封装

基于 OpenAI 兼容 API 的大语言模型服务，支持：
- 通用对话（chat）
- RAG 增强对话（chat_with_rag）
- 意图识别（extract_intent）
- 健康预警生成（generate_health_alert）
- 可对接 DeepSeek、通义千问等 OpenAI 兼容 API
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class LLMService:
    """大语言模型服务

    封装 OpenAI 兼容 API，提供MedSignal所需的各项 LLM 能力。
    """

    # 意图识别的系统提示词
    INTENT_SYSTEM_PROMPT = """你是一个医保智能助手的意图识别模块。根据用户消息，判断用户意图并返回 JSON 格式结果。

可能的意图类型：
- coverage: 医保报销、待遇、缴费、个人账户等
- claims: 理赔、报销流程、发票、预审等
- health_profile: 健康画像、体检、慢病管理、预警等
- policy: 政策查询、规定、通知、异地就医、参保等
- governance: 用户要求治理病历、脱敏、结构化病历数据（如"帮我治理这段病历"）
- security: 数据授权、隐私、安全、审计等
- body: 用户描述自己身体部位的症状/检查结果（如"查出肺结节"、"右肩疼"），或要求记录、查看、对比自己的健康档案

请返回如下 JSON 格式：
{
  "intent": "意图类型",
  "confidence": 0.0-1.0的置信度,
  "keywords": ["提取的关键词列表"],
  "sub_intent": "子意图描述"
}

只返回 JSON，不要其他内容。"""

    # RAG 系统提示词模板
    RAG_SYSTEM_TEMPLATE = """你是"MedSignal"——一个专业的医保政策智能助手。你的职责是基于检索到的政策资料，准确、专业地回答用户关于医保政策的问题。

## 回答要求：
1. 基于下方【参考资料】中的信息回答，不要编造政策内容
2. 如果参考资料不足以回答问题，请明确告知用户并建议咨询当地医保局
3. 引用具体政策时，标注来源和文号
4. 使用清晰的结构化格式（分点、分段）
5. 对涉及金额、比例等关键数据，务必准确引用

## 参考资料：
{context}

## 回答格式：
- 先给出直接回答
- 然后列出相关政策依据（标注来源）
- 最后给出建议或注意事项"""

    # 健康预警系统提示词
    HEALTH_ALERT_PROMPT = """你是MedSignal的健康管理模块。根据用户的健康数据，生成个性化的健康预警和建议。

请分析以下用户健康数据，返回 JSON 格式的预警结果：
{
  "risk_level": "low/medium/high",
  "alerts": [
    {
      "type": "预警类型",
      "description": "预警描述",
      "suggestion": "建议措施",
      "related_policy": "相关政策提示"
    }
  ],
  "overall_assessment": "总体健康评估"
}

只返回 JSON，不要其他内容。"""

    def __init__(self, api_key: str, base_url: str, model: str,
                 fallback_api_key: str = "", fallback_base_url: str = "",
                 fallback_model: str = ""):
        """初始化 LLM 服务

        Args:
            api_key: 主力模型 API 密钥（aiping 网关 Kimi-K3）
            base_url: 主力模型 API 基础地址
            model: 主力模型名称
            fallback_api_key: 备选模型 API 密钥（阿里云 DashScope）
            fallback_base_url: 备选模型 API 基础地址
            fallback_model: 备选模型名称
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = None
        self._initialized = False

        # 备选模型（阿里云 DashScope）
        self._fallback_api_key = fallback_api_key
        self._fallback_base_url = fallback_base_url.rstrip("/")
        self._fallback_model = fallback_model
        self._fallback_client = None
        self._fallback_initialized = False

        self._init_client()
        if fallback_api_key and not self._is_placeholder_key(fallback_api_key):
            self._init_fallback_client()
        elif fallback_api_key:
            # 占位符密钥（如 your-xxx）会导致每次主力模型波动都附带 401 报错噪音，直接禁用备选层
            logger.warning("备选模型密钥为占位符，已禁用 DashScope 备选层（主力模型不受影响）")
            self._fallback_initialized = False

    @staticmethod
    def _is_placeholder_key(key: str) -> bool:
        """识别 .env 模板里未替换的占位符密钥。"""
        k = (key or "").strip().lower()
        return (not k) or k.startswith("your-") or k in ("placeholder", "sk-xxx", "changeme")

    def _init_client(self):
        """初始化主力模型 OpenAI 客户端（aiping 网关 Kimi-K3）

        推理模型单次生成实测 20–60s：timeout 必须大于单次生成时长，且禁用 SDK 重试，
        否则 30s 超时×3 次重试会把单次对话拖到 90s+，击穿路由层超时保护。
        """
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=90.0,
                max_retries=0,
            )
            self._initialized = True
            logger.info("主力 LLM 客户端初始化成功 (model=%s, base_url=%s)", self._model, self._base_url)
        except ImportError:
            logger.error("openai 库未安装，LLM 服务不可用")
            self._initialized = False
        except Exception as e:
            logger.error("主力 LLM 客户端初始化失败: %s", e)
            self._initialized = False

    def _init_fallback_client(self):
        """初始化备选模型 OpenAI 客户端（阿里云 DashScope）"""
        try:
            from openai import OpenAI
            self._fallback_client = OpenAI(
                api_key=self._fallback_api_key,
                base_url=self._fallback_base_url,
                timeout=60.0,
                max_retries=0,
            )
            self._fallback_initialized = True
            logger.info("备选 LLM 客户端初始化成功 (model=%s, base_url=%s)", self._fallback_model, self._fallback_base_url)
        except ImportError:
            logger.error("openai 库未安装，备选 LLM 服务不可用")
            self._fallback_initialized = False
        except Exception as e:
            logger.error("备选 LLM 客户端初始化失败: %s", e)
            self._fallback_initialized = False

    # ------------------------------------------------------------------
    # 核心对话方法
    # ------------------------------------------------------------------

    async def chat(self, messages: list[dict], temperature: float = 0.7) -> str:
        """通用对话接口，支持主力/备选模型自动切换

        Args:
            messages: 消息列表，格式 [{"role": "user", "content": "..."}]
            temperature: 生成温度，越高越随机

        Returns:
            模型生成的回复文本
        """
        # 尝试主力模型（aiping 网关 Kimi-K3，推理模型需较大 max_tokens：
        # 思考过程与正文共用额度，2048 会被 reasoning 吃光导致正文为空）。
        # 同步 SDK 必须用 asyncio.to_thread 包装，否则会阻塞事件循环，
        # 导致路由层的 asyncio.wait_for 超时保护失效（曾引发线上 504）。
        if self._initialized:
            try:
                response = await asyncio.to_thread(
                    self._client.chat.completions.create,
                    model=self._model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=4096,
                )
                choice = response.choices[0]
                # 推理模型：content 可能为空，reasoning 字段包含思考过程
                content = choice.message.content
                if not content and hasattr(choice.message, 'reasoning') and choice.message.reasoning:
                    # content 为空但 reasoning 有内容，说明模型在思考中耗尽了 token
                    content = choice.message.reasoning
                if content:
                    logger.debug("主力 LLM 响应长度: %d 字符", len(content))
                    return content.strip()
                logger.warning("主力 LLM 返回空内容")
            except Exception as e:
                logger.warning("主力 LLM 对话失败，尝试备选模型: %s", e)

        # 尝试备选模型（阿里云 DashScope）
        if self._fallback_initialized:
            try:
                response = await asyncio.to_thread(
                    self._fallback_client.chat.completions.create,
                    model=self._fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=2048,
                )
                content = response.choices[0].message.content
                if content:
                    logger.debug("备选 LLM 响应长度: %d 字符", len(content))
                    return content.strip()
            except Exception as e:
                logger.error("备选 LLM 对话也失败: %s", e)
                # 不把原始错误透传给用户：抛异常让调用方（编排器各智能体）
                # 的 try/except 降级分支生效，走规则引擎/离线回答
                raise RuntimeError("LLM 服务暂不可用（主力与备选均失败），已触发离线降级") from e

        raise RuntimeError("LLM 服务未初始化，无法调用")

    async def chat_vision(self, messages: list[dict], temperature: float = 0.1) -> str:
        """多模态（图片）对话：走阿里云 DashScope 视觉模型（qwen-vl-*）。

        messages 的 content 为 OpenAI 兼容多段格式：
        [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}, {"type": "text", "text": "..."}]
        无 DashScope 配置时抛 RuntimeError，由调用方降级。
        """
        from app.config import settings

        client = self._fallback_client if self._fallback_initialized else None
        if client is None and self._initialized and "dashscope" in self._base_url:
            client = self._client
        if client is None:
            raise RuntimeError("视觉模型不可用：未配置 DASHSCOPE_API_KEY")

        response = client.chat.completions.create(
            model=settings.DASHSCOPE_VL_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=2048,
        )
        return (response.choices[0].message.content or "").strip()

    async def chat_with_rag(
        self,
        system_prompt: str,
        user_message: str,
        context: list[str],
    ) -> str:
        """RAG 增强对话

        将检索到的上下文注入系统提示词，结合用户问题生成回答。

        Args:
            system_prompt: 基础系统提示词
            user_message: 用户消息
            context: 检索到的上下文片段列表

        Returns:
            包含来源引用的回答
        """
        if not self._initialized:
            # 降级：返回基于上下文的简单拼接
            if context:
                return "基于检索到的资料：\n\n" + "\n---\n".join(context)
            return "LLM 服务暂不可用，请检查 API 配置。"

        # 将上下文片段拼接并注入系统提示词
        context_text = "\n\n".join(
            f"[资料 {i+1}]\n{ctx}" for i, ctx in enumerate(context)
        )

        # 使用 RAG 模板或自定义系统提示词
        if "{context}" in system_prompt:
            full_system_prompt = system_prompt.format(context=context_text)
        else:
            full_system_prompt = self.RAG_SYSTEM_TEMPLATE.format(context=context_text)

        messages = [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_message},
        ]

        return await self.chat(messages, temperature=0.3)  # RAG 场景用较低温度保证准确性

    # ------------------------------------------------------------------
    # 意图识别
    # ------------------------------------------------------------------

    async def extract_intent(self, message: str) -> dict:
        """从用户消息中提取意图

        Args:
            message: 用户消息文本

        Returns:
            意图识别结果字典，包含 intent/confidence/keywords/sub_intent
        """
        if not self._initialized:
            # 降级：基于关键词的简单意图识别
            return self._fallback_intent(message)

        try:
            messages = [
                {"role": "system", "content": self.INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ]
            response = await self.chat(messages, temperature=0.1)

            # 解析 JSON 响应
            result = self._parse_json_response(response)
            if result and "intent" in result:
                logger.info("意图识别: intent=%s, confidence=%.2f", result["intent"], result.get("confidence", 0))
                return result

            # JSON 解析失败，回退到关键词匹配
            return self._fallback_intent(message)
        except Exception as e:
            logger.error("意图识别失败: %s", e)
            return self._fallback_intent(message)

    @staticmethod
    def _fallback_intent(message: str) -> dict:
        """基于关键词的简单意图识别（降级方案）"""
        intent_keywords = {
            "coverage": ["报销", "待遇", "报销比例", "起付线", "封顶线", "医保卡", "个人账户", "缴费"],
            "claims": ["理赔", "报销流程", "发票", "OCR", "上传", "预审", "报销材料"],
            "health_profile": ["健康", "体检", "画像", "慢病", "用药", "趋势", "预警"],
            "policy": ["政策", "规定", "通知", "办法", "文件", "异地", "参保"],
            "security": ["授权", "隐私", "数据安全", "审计", "权限"],
        }

        best_intent = "coverage"
        best_score = 0
        matched_keywords = []

        for intent, keywords in intent_keywords.items():
            score = sum(1 for kw in keywords if kw in message)
            if score > best_score:
                best_score = score
                best_intent = intent
                matched_keywords = [kw for kw in keywords if kw in message]

        return {
            "intent": best_intent,
            "confidence": min(best_score / 3.0, 1.0),
            "keywords": matched_keywords,
            "sub_intent": message[:20],
        }

    # ------------------------------------------------------------------
    # 健康预警
    # ------------------------------------------------------------------

    async def generate_health_alert(self, user_data: dict) -> dict:
        """根据用户健康数据生成健康预警

        Args:
            user_data: 用户健康数据字典，包含年龄、慢病、用药等信息

        Returns:
            预警结果字典，包含 risk_level/alerts/overall_assessment
        """
        if not self._initialized:
            # 降级：基于规则的简单预警
            return self._fallback_health_alert(user_data)

        try:
            # 将用户数据序列化为可读文本
            user_data_text = json.dumps(user_data, ensure_ascii=False, indent=2)

            messages = [
                {"role": "system", "content": self.HEALTH_ALERT_PROMPT},
                {"role": "user", "content": f"请分析以下用户健康数据：\n{user_data_text}"},
            ]
            response = await self.chat(messages, temperature=0.3)

            result = self._parse_json_response(response)
            if result and "risk_level" in result:
                logger.info("健康预警: risk_level=%s, alerts=%d", result["risk_level"], len(result.get("alerts", [])))
                return result

            return self._fallback_health_alert(user_data)
        except Exception as e:
            logger.error("健康预警生成失败: %s", e)
            return self._fallback_health_alert(user_data)

    @staticmethod
    def _fallback_health_alert(user_data: dict) -> dict:
        """基于规则的简单健康预警（降级方案）"""
        alerts = []
        chronic_diseases = user_data.get("chronic_diseases", [])

        # 高血压预警
        if "高血压" in chronic_diseases:
            alerts.append({
                "type": "慢病管理",
                "description": "您有高血压病史，建议定期监测血压",
                "suggestion": "按时服药，低盐饮食，定期复查",
                "related_policy": "高血压可申请门诊慢特病待遇，报销比例提高5个百分点",
            })

        # 糖尿病预警
        if "糖尿病" in chronic_diseases:
            alerts.append({
                "type": "慢病管理",
                "description": "您有糖尿病病史，建议定期监测血糖",
                "suggestion": "控制饮食，规律运动，定期检查并发症",
                "related_policy": "糖尿病可申请门诊慢特病待遇，年度支付限额5000元",
            })

        # 年龄预警
        age = user_data.get("age", 0)
        if age >= 65:
            alerts.append({
                "type": "年龄风险",
                "description": "65岁以上人群属于医疗高风险群体",
                "suggestion": "建议每年体检，关注心脑血管健康",
                "related_policy": "退休人员医保报销比例高于在职人员",
            })

        risk_level = "high" if len(alerts) >= 2 else ("medium" if alerts else "low")

        return {
            "risk_level": risk_level,
            "alerts": alerts,
            "overall_assessment": f"检测到 {len(alerts)} 项风险提示" if alerts else "暂无明显风险",
        }

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(text: str) -> dict | list | None:
        """尝试从 LLM 响应中解析 JSON（对象或数组）

        处理可能的 markdown 代码块包裹等情况。
        """
        # 去除 markdown 代码块标记
        text = text.strip()
        if text.startswith("```"):
            # 去除首行 ```json 和末行 ```
            lines = text.split("\n")
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 部分（对象优先，其次数组）
            import re
            for pattern in (r"\{[\s\S]*\}", r"\[[\s\S]*\]"):
                json_match = re.search(pattern, text)
                if json_match:
                    try:
                        return json.loads(json_match.group())
                    except json.JSONDecodeError:
                        continue
            logger.warning("JSON 解析失败: %s", text[:100])
            return None

    @property
    def is_available(self) -> bool:
        """LLM 服务是否可用"""
        return self._initialized
