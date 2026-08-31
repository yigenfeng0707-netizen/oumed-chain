"""视觉模型服务单元测试（瓯医数链 Agent · GLM-4.6V 影像解读）

覆盖：
- 降级路径（未配置 Key / DEMO_OFFLINE / 空影像 / 调用失败 → None，不影响主流程）
- 正常解读（mock 客户端返回内容 / 推理模型 reasoning 降级读取）
- 请求构造（裸 base64 自动补 data URI 前缀 / 多模态消息结构）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.services import vision_service as vs
from app.services.vision_service import VisionService


@pytest.fixture(autouse=True)
def _reset_singleton():
    """每个用例前重置模块级单例，避免用例间状态泄漏"""
    vs._vision_service = None
    yield
    vs._vision_service = None


class _FakeMessage:
    def __init__(self, content, reasoning=None):
        self.content = content
        self.reasoning = reasoning


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    """记录调用参数的 mock completions 接口"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


def _make_service(api_key="test-key"):
    svc = VisionService(api_key=api_key, base_url="https://example.com/api/v1", model="GLM-4.6V")
    return svc


# ============================================================
# 1. 降级路径
# ============================================================

class TestDegradation:
    """视觉模型降级行为测试"""

    def test_no_api_key_unavailable(self):
        """未配置 API Key 时服务不可用"""
        svc = VisionService(api_key="", base_url="https://example.com/api/v1", model="GLM-4.6V")
        assert svc.is_available is False

    def test_no_api_key_interpret_returns_none(self):
        """未配置 Key 时解读返回 None（不抛异常）"""
        svc = VisionService(api_key="", base_url="https://example.com/api/v1", model="GLM-4.6V")
        assert svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片") is None

    def test_empty_image_returns_none(self):
        """空影像返回 None"""
        svc = _make_service()
        assert svc.interpret_imaging_study("", "胸部 X 光片") is None

    def test_call_failure_returns_none(self):
        """客户端调用异常时返回 None（主流程不受影响）"""
        svc = _make_service()
        svc._client = _FakeClient(_FakeCompletions(error=RuntimeError("network down")))
        result = svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片")
        assert result is None

    def test_empty_content_returns_none(self):
        """模型返回空内容且无 reasoning 时返回 None"""
        svc = _make_service()
        svc._client = _FakeClient(_FakeCompletions(_FakeResponse(_FakeMessage(None))))
        assert svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片") is None

    def test_get_vision_service_offline_mode(self, monkeypatch):
        """DEMO_OFFLINE 模式下 get_vision_service 返回 None"""
        monkeypatch.setattr(vs.settings, "DEMO_OFFLINE", True)
        assert vs.get_vision_service() is None

    def test_get_vision_service_no_key(self, monkeypatch):
        """未配置 VISION_API_KEY 时 get_vision_service 返回 None"""
        monkeypatch.setattr(vs.settings, "DEMO_OFFLINE", False)
        monkeypatch.setattr(vs.settings, "VISION_API_KEY", "")
        assert vs.get_vision_service() is None


# ============================================================
# 2. 正常解读
# ============================================================

class TestInterpretation:
    """视觉模型解读行为测试"""

    def test_interpret_returns_content(self):
        """正常返回解读文本（去除首尾空白）"""
        svc = _make_service()
        svc._client = _FakeClient(_FakeCompletions(_FakeResponse(_FakeMessage("  影像所见：右肺结节。  "))))
        result = svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片", "肺结节（中危）")
        assert result == "影像所见：右肺结节。"

    def test_reasoning_fallback(self):
        """推理模型 content 为空时读取 reasoning 字段"""
        svc = _make_service()
        svc._client = _FakeClient(_FakeCompletions(_FakeResponse(_FakeMessage(None, reasoning="思考后结论"))))
        result = svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片")
        assert result == "思考后结论"

    def test_bare_base64_gets_prefix(self):
        """裸 base64 影像自动补 data URI 前缀"""
        svc = _make_service()
        comp = _FakeCompletions(_FakeResponse(_FakeMessage("解读")))
        svc._client = _FakeClient(comp)
        svc.interpret_imaging_study("QUJD", "胸部 X 光片")
        url = comp.calls[0]["messages"][1]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,QUJD")

    def test_multimodal_message_structure(self):
        """请求为标准多模态消息结构（system + user[text+image]）"""
        svc = _make_service()
        comp = _FakeCompletions(_FakeResponse(_FakeMessage("解读")))
        svc._client = _FakeClient(comp)
        svc.interpret_imaging_study("data:image/png;base64,xxxx", "肺 CT", "磨玻璃影（高危）")
        messages = comp.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        assert "影像解读助手" in messages[0]["content"]
        user_content = messages[1]["content"]
        assert user_content[0]["type"] == "text"
        assert "肺 CT" in user_content[0]["text"]
        assert "磨玻璃影" in user_content[0]["text"]
        assert user_content[1]["type"] == "image_url"

    def test_empty_findings_summary_handled(self):
        """检测结果为空时提示未检出明确病灶"""
        svc = _make_service()
        comp = _FakeCompletions(_FakeResponse(_FakeMessage("解读")))
        svc._client = _FakeClient(comp)
        svc.interpret_imaging_study("data:image/png;base64,xxxx", "胸部 X 光片", "")
        text = comp.calls[0]["messages"][1]["content"][0]["text"]
        assert "未检出明确病灶" in text

    def test_get_vision_service_singleton(self, monkeypatch):
        """get_vision_service 返回可用单例（重复调用同一实例）"""
        monkeypatch.setattr(vs.settings, "DEMO_OFFLINE", False)
        monkeypatch.setattr(vs.settings, "VISION_API_KEY", "test-key")
        monkeypatch.setattr(vs.settings, "VISION_BASE_URL", "https://example.com/api/v1")
        monkeypatch.setattr(vs.settings, "VISION_MODEL", "GLM-4.6V")
        a = vs.get_vision_service()
        b = vs.get_vision_service()
        assert a is not None and a.is_available
        assert a is b
