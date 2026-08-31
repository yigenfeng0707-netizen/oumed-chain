"""
瓯医数链 - 视觉模型服务（多模态影像理解）

基于 OpenAI 兼容多模态 API 的视觉大模型（GLM-4.6V 等），为医学影像
AI 标注工作流提供自然语言"影像所见"解读，与影像引擎的确定性病灶
检测互补：

- interpret_imaging_study：影像 + AI 检测摘要 → 叙事化影像解读
- 全链路降级：未配置 Key / DEMO_OFFLINE / 调用失败 → 返回 None，
  不影响影像分析主流程（与 LLMService 三级降级策略一致）
"""

import logging

from app.config import settings

logger = logging.getLogger(__name__)


class VisionService:
    """视觉大模型服务（OpenAI 兼容多模态 API）"""

    SYSTEM_PROMPT = (
        "你是 瓯医数链 的医学影像解读助手。基于收到的医学影像与 AI 检测结果，"
        "用简体中文生成简明的「影像所见」解读（120 字以内）：说明可疑病灶的位置"
        "与表现，给出就诊建议方向。不要编造影像中不存在的发现；若 AI 检测结果"
        "为空且影像未见明显异常，请如实说明。结尾必须注明：本解读由 AI 生成，"
        "仅供筛查参考，须由持证医师复核确认。"
    )

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: float = 60.0, max_tokens: int = 1024):
        """初始化视觉模型服务

        Args:
            api_key: 视觉模型 API 密钥（aiping 网关 GLM-4.6V）
            base_url: API 基础地址（OpenAI 兼容）
            model: 视觉模型名称
            timeout: 单次调用超时（秒），避免拖慢影像分析主流程
            max_tokens: 生成 token 上限（推理模型需预留思考空间）
        """
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._client = None
        self._initialized = False
        self._init_client()

    def _init_client(self):
        if not self._api_key:
            logger.info("视觉模型未配置 API Key，影像解读功能自动关闭（降级）")
            return
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout,
            )
            self._initialized = True
            logger.info("视觉模型客户端初始化成功 (model=%s, base_url=%s)", self._model, self._base_url)
        except ImportError:
            logger.error("openai 库未安装，视觉模型服务不可用")
        except Exception as e:
            logger.error("视觉模型客户端初始化失败: %s", e)

    @property
    def is_available(self) -> bool:
        """视觉模型服务是否可用"""
        return self._initialized

    def interpret_imaging_study(
        self,
        image_data_uri: str,
        study_label: str,
        findings_summary: str = "",
    ) -> str | None:
        """影像 + AI 检测摘要 → 自然语言影像解读

        Args:
            image_data_uri: data URI 形式的影像（data:image/png;base64,...），
                            或裸 base64（自动补 PNG 前缀）
            study_label: 检查类型中文名（如"胸部 X 光片"）
            findings_summary: AI 检测结果摘要文本（可为空）

        Returns:
            解读文本；服务不可用或调用失败时返回 None（主流程不受影响）
        """
        if not self._initialized or not image_data_uri:
            return None

        if not image_data_uri.startswith("data:"):
            image_data_uri = "data:image/png;base64," + image_data_uri

        user_text = (
            f"检查类型：{study_label}\n"
            f"AI 检测结果：{findings_summary or '未检出明确病灶'}\n"
            "请生成影像所见解读。"
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": image_data_uri}},
                        ],
                    },
                ],
                temperature=0.2,
                max_tokens=self._max_tokens,
            )
            choice = response.choices[0]
            content = choice.message.content
            # 推理模型：content 可能为空，reasoning 字段包含思考过程
            if not content and hasattr(choice.message, "reasoning") and choice.message.reasoning:
                content = choice.message.reasoning
            if content:
                logger.info("视觉模型影像解读生成成功 (%d 字符)", len(content))
                return content.strip()
            logger.warning("视觉模型返回空内容")
            return None
        except Exception as e:
            logger.warning("视觉模型影像解读失败（已降级跳过）: %s", e)
            return None


# ============================================================
# 单例获取（供 Router 使用，与 orchestrator 懒加载模式一致）
# ============================================================

_vision_service: VisionService | None = None


def get_vision_service() -> VisionService | None:
    """获取视觉模型服务单例。

    DEMO_OFFLINE 模式或未配置 Key 时返回 None，
    调用方据此走"无解读"降级路径。
    """
    global _vision_service
    if settings.DEMO_OFFLINE:
        return None
    if _vision_service is None:
        _vision_service = VisionService(
            api_key=settings.VISION_API_KEY,
            base_url=settings.VISION_BASE_URL,
            model=settings.VISION_MODEL,
        )
    return _vision_service if _vision_service.is_available else None
