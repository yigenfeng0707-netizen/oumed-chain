"""
瓯医数链 - OCR 服务封装

基于 OCR.space API 的票据识别服务，支持：
- 医疗发票/票据图片识别
- 中文文字提取
- 结构化费用信息解析
- 降级到 mock 数据
"""

import logging
from typing import TYPE_CHECKING

import httpx

from app.config import settings

if TYPE_CHECKING:
    from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class OCRService:
    """OCR 票据识别服务

    使用 OCR.space API 识别医疗发票图片，
    并将识别结果解析为结构化的费用信息。
    """

    def __init__(self, api_key: str = "", api_url: str = ""):
        self._api_key = api_key or settings.OCR_API_KEY
        self._api_url = api_url or settings.OCR_API_URL
        self._initialized = bool(self._api_key)
        self._llm: LLMService | None = None

        if self._initialized:
            logger.info("OCR 服务初始化成功 (url=%s)", self._api_url)
        else:
            logger.warning("OCR API Key 未配置，将使用 mock 数据降级方案")

    def _get_llm(self):
        """获取或懒加载 LLM 实例（复用单例）"""
        if self._llm is None:
            from app.services.llm_service import LLMService
            self._llm = LLMService(
                api_key=settings.DASHSCOPE_API_KEY,
                base_url=settings.DASHSCOPE_BASE_URL,
                model=settings.DASHSCOPE_MODEL,
            )
        return self._llm

    async def recognize_text(self, image_bytes: bytes, filename: str = "image.jpg") -> str:
        """调用 OCR.space 返回原始识别文本；未配置/失败返回空串（绝不返回 mock 文本）。

        供档案管家等只需要原文的场景复用。
        """
        if not self._initialized:
            return ""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    self._api_url,
                    data={
                        "apikey": self._api_key,
                        "language": "chs",  # 简体中文
                        "isOverlayRequired": "false",
                        "OCREngine": "2",  # Engine 2 对中文支持更好
                    },
                    files={"file": (filename, image_bytes, "image/jpeg")},
                )
                response.raise_for_status()
                result = response.json()

            if result.get("IsErroredOnProcessing"):
                logger.error("OCR 处理错误: %s", result.get("ErrorMessage"))
                return ""
            parsed_results = result.get("ParsedResults", [])
            text = parsed_results[0].get("ParsedText", "") if parsed_results else ""
            if text:
                logger.info("OCR 识别成功，文本长度: %d 字符", len(text))
            else:
                logger.warning("OCR 识别文本为空")
            return text
        except httpx.TimeoutException:
            logger.error("OCR 请求超时")
        except httpx.HTTPStatusError as e:
            logger.error("OCR HTTP 错误: %s", e)
        except Exception as e:
            logger.error("OCR 处理失败: %s", e)
        return ""

    async def recognize(self, image_bytes: bytes, filename: str = "receipt.jpg") -> dict:
        """识别医疗发票图片

        Args:
            image_bytes: 图片二进制数据
            filename: 文件名

        Returns:
            结构化的 OCR 识别结果
        """
        if not self._initialized:
            return self._mock_ocr_result()

        text = await self.recognize_text(image_bytes, filename)
        if not text:
            return self._mock_ocr_result()

        # 将原始文本交给 LLM 解析为结构化数据
        try:
            return await self._parse_with_llm(text)
        except Exception as e:
            logger.error("OCR 处理失败: %s", e)
            return self._mock_ocr_result()

    async def _parse_with_llm(self, ocr_text: str) -> dict:
        """使用 LLM 将 OCR 原始文本解析为结构化费用信息

        Args:
            ocr_text: OCR 识别的原始文本

        Returns:
            结构化的费用信息字典
        """
        try:
            from app.services.llm_service import LLMService

            llm = self._get_llm()

            parse_prompt = f"""请从以下医疗发票OCR识别文本中提取结构化信息，返回JSON格式：

{ocr_text}

请返回如下JSON格式（只返回JSON，不要其他内容）：
{{
  "hospital": "医院名称",
  "date": "就诊日期(YYYY-MM-DD)",
  "patient_name": "患者姓名",
  "department": "科室",
  "visit_type": "门诊/住院",
  "items": [
    {{"name": "项目名称", "amount": 金额}}
  ],
  "total_amount": 总金额
}}

如果某个字段无法识别，设为空字符串或0。"""

            response = await llm.chat(
                messages=[{"role": "user", "content": parse_prompt}],
                temperature=0.1,
            )

            # 解析 LLM 返回的 JSON
            result = LLMService._parse_json_response(response)
            if result and "items" in result:
                result["confidence"] = 0.90
                return result

        except Exception as e:
            logger.error("LLM 解析 OCR 结果失败: %s", e)

        # 降级：返回原始文本 + mock 结构化数据
        return {
            "raw_text": ocr_text,
            **self._mock_ocr_result(),
        }

    @staticmethod
    def _mock_ocr_result() -> dict:
        """Mock OCR 结果（降级方案）"""
        return {
            "hospital": "某市第一人民医院",
            "date": "2024-12-10",
            "patient_name": "张阿姨",
            "department": "内分泌科",
            "visit_type": "门诊",
            "items": [
                {"name": "挂号费", "amount": 25.00},
                {"name": "诊查费", "amount": 35.00},
                {"name": "血糖检测", "amount": 45.00},
                {"name": "糖化血红蛋白检测", "amount": 80.00},
                {"name": "二甲双胍缓释片(0.5g×30片)", "amount": 38.50},
                {"name": "缬沙坦胶囊(80mg×7粒)", "amount": 30.00},
            ],
            "total_amount": 253.50,
            "confidence": 0.95,
        }


# 全局 OCR 服务实例
_ocr_service: OCRService | None = None


def get_ocr_service() -> OCRService:
    """获取 OCR 服务单例"""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = OCRService()
    return _ocr_service
