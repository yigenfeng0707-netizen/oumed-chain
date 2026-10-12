"""P2.5 LLM 生产化：全局并发限流 + 知识库冷启动预热（不依赖真实网络）"""

import asyncio
import threading

import pytest

from app.services.llm_service import LLMService
from app.services.orchestrator import Orchestrator
from app.services.knowledge_base import KnowledgeBase


class _FakeCompletions:
    """假的 chat.completions：同步阻塞一小段时间并统计并发峰值。"""

    def __init__(self, tracker: dict, fail: bool = False, delay: float = 0.05):
        self._tracker = tracker
        self._fail = fail
        self._delay = delay
        self._lock = threading.Lock()

    def create(self, **kwargs):  # 同步签名，与 openai SDK 一致
        with self._lock:
            self._tracker["inflight"] += 1
            self._tracker["max_inflight"] = max(
                self._tracker["max_inflight"], self._tracker["inflight"])
        try:
            import time
            time.sleep(self._delay)
            if self._fail:
                raise RuntimeError("simulated upstream failure")
            return type("R", (), {"choices": [type(
                "C", (), {"message": type("M", (), {"content": "ok", "reasoning": None})()})()]})()
        finally:
            with self._lock:
                self._tracker["inflight"] -= 1


class _FakeClient:
    def __init__(self, tracker: dict, fail: bool = False):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(tracker, fail=fail)


async def test_chat_respects_global_concurrency_limit():
    tracker = {"inflight": 0, "max_inflight": 0}
    svc = LLMService(api_key="k", base_url="https://example.com", model="m",
                     max_concurrency=2)
    svc._client = _FakeClient(tracker)  # 替换为假客户端（_initialized 已为 True）

    results = await asyncio.gather(*[
        svc.chat([{"role": "user", "content": f"q{i}"}]) for i in range(6)
    ])
    assert results == ["ok"] * 6
    assert tracker["max_inflight"] <= 2  # 超出的请求必须排队
    assert svc._in_flight == 0  # 结束后槽位全部归还


async def test_fallback_path_also_limited():
    """主力持续失败切备选时，同一信号量覆盖完整调用链。"""
    tracker = {"inflight": 0, "max_inflight": 0}
    svc = LLMService(api_key="k", base_url="https://example.com", model="m",
                     fallback_api_key="fk", fallback_base_url="https://fallback.com",
                     fallback_model="fm", max_concurrency=2)
    svc._client = _FakeClient(tracker, fail=True)
    svc._fallback_client = _FakeClient(tracker)

    results = await asyncio.gather(*[
        svc.chat([{"role": "user", "content": f"q{i}"}]) for i in range(4)
    ])
    assert results == ["ok"] * 4
    assert tracker["max_inflight"] <= 2


async def test_chat_raises_when_both_disabled():
    svc = LLMService(api_key="k", base_url="https://example.com", model="m",
                     max_concurrency=2)
    svc._initialized = False
    svc._fallback_initialized = False
    with pytest.raises(RuntimeError, match="未初始化"):
        await svc.chat([{"role": "user", "content": "q"}])


async def test_chat_vision_uses_limiter(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "VISION_API_KEY", "vk", raising=False)
    tracker = {"inflight": 0, "max_inflight": 0}
    svc = LLMService(api_key="k", base_url="https://example.com", model="m",
                     max_concurrency=2)
    svc._vision_client = _FakeClient(tracker)

    results = await asyncio.gather(*[
        svc.chat_vision([{"role": "user", "content": "img"}]) for _ in range(4)
    ])
    assert results == ["ok"] * 4
    assert tracker["max_inflight"] <= 2  # 视觉通道与文本通道共用限流


async def test_chat_vision_requires_key(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "VISION_API_KEY", "", raising=False)
    svc = LLMService(api_key="k", base_url="https://example.com", model="m",
                     max_concurrency=2)
    with pytest.raises(RuntimeError, match="视觉模型不可用"):
        await svc.chat_vision([{"role": "user", "content": "img"}])


# ---------------- 知识库冷启动预热 ----------------

def _fresh_kb() -> KnowledgeBase:
    kb = KnowledgeBase(embedding_api_key="", embedding_base_url="", embedding_model="")
    return kb


async def test_prewarm_skipped_when_not_initialized():
    kb = _fresh_kb()
    assert await kb.prewarm() is False  # 未初始化直接跳过，不触模型加载


async def test_prewarm_loads_embedding_model():
    kb = _fresh_kb()
    kb._initialized = True
    calls = []
    kb._generate_embeddings = lambda texts: calls.append(list(texts)) or [[0.1, 0.2]]
    assert await kb.prewarm() is True
    assert calls and calls[0]  # 预热确实执行了一次嵌入生成


async def test_prewarm_failure_swallowed():
    kb = _fresh_kb()
    kb._initialized = True

    def _boom(_texts):
        raise RuntimeError("onnx download failed")

    kb._generate_embeddings = _boom
    assert await kb.prewarm() is False  # 预热失败只返回 False，不抛异常


async def test_orchestrator_prewarm_safe_without_kb():
    orch = Orchestrator()
    orch._kb = None
    await orch._prewarm_kb()  # 不应抛异常
