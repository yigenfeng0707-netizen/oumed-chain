"""节点 Agent TLS/mTLS 上下文构造（P1.2 传输安全）。

仅用标准库 ssl——与 Agent 零第三方依赖原则一致：
- 默认（三个环境变量全留空）：系统 CA 验证服务端证书的单向 TLS；
- TLS_CA_CERT 指定平台自建 CA：用该 CA 验证服务端（私有部署推荐）；
- TLS_CLIENT_CERT/TLS_CLIENT_KEY 同时配置：加载客户端证书发起 mTLS 双向认证，
  中间人攻击者没有 CA 签发的客户端证书，TLS 握手直接失败。

上下文按模块缓存（证书文件进程生命周期内不变），避免每请求重复读盘。
"""

from __future__ import annotations

import ssl

from node_agent import config

_context: ssl.SSLContext | None = None


def build_ssl_context() -> ssl.SSLContext:
    """按环境变量构造 SSLContext（不发起网络握手，纯本地构造）。"""
    context = ssl.create_default_context(cafile=config.TLS_CA_CERT or None)
    if config.TLS_CLIENT_CERT and config.TLS_CLIENT_KEY:
        context.load_cert_chain(config.TLS_CLIENT_CERT, config.TLS_CLIENT_KEY)
    elif config.TLS_CLIENT_CERT or config.TLS_CLIENT_KEY:
        raise ValueError("mTLS 配置不完整：TLS_CLIENT_CERT 与 TLS_CLIENT_KEY 必须同时配置")
    return context


def ssl_context() -> ssl.SSLContext:
    """带缓存的 SSLContext（测试可用 reset_cache() 切换环境变量后重建）。"""
    global _context
    if _context is None:
        _context = build_ssl_context()
    return _context


def reset_cache() -> None:
    """清空缓存上下文（测试/热更新配置用）。"""
    global _context
    _context = None
