"""P1.2 节点认证与传输安全测试（mTLS + 密钥轮换）：

- 信封 v2：enc2:key_id:nonce:ct 双向互通（多版本密钥环过渡期）；
- 密钥轮换验收：旧密钥移出密钥环后历史信封永久不可解密（泄露场景）；
- enc1 旧格式过渡兼容；明文兼容双模保持；密钥环解析（非法格式即报错）；
- 节点侧：key_id 嵌入 / NODE_KEY_ID 显式指定（配置漂移可发现）/ 对端密钥不匹配提示；
- mTLS（socketpair 真实 TLS 握手，零网络依赖）：
  * 默认单向 TLS（系统 CA 校验服务端）；
  * 联邦 CA 客户端证书握手成功 → 服务端可见对端证书（双向认证生效）；
  * 中间人模拟：无客户端证书 / 非联邦 CA → 握手直接失败（传输层拒绝）。

纯本地构造与握手，不依赖真实网络与 nginx。
"""

import base64
import os
import socket
import ssl
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "node_agent"))

from app.config import settings  # noqa: E402
from app.crypto import parse_key  # noqa: E402
from app.services.federation_coordinator import (active_key, envelope_key_id,
                                                 keyring, open_envelope,
                                                 seal_envelope)
from node_agent import config as agent_config
from node_agent import crypto as agent_crypto
from node_agent import tls as agent_tls

KEY_OLD = "Zx9vQ2FyZVRlYW1LZXlOb3RTb0xvbmdUcnVzdGVkMQ=="   # 32 字节 base64（旧密钥）
KEY_NEW = "TmV3Um90YXRlZEtleUZvckZlZGVyYXRpb25QYXJhbTEyMw=="  # 32 字节 base64（新密钥）
KID_OLD = envelope_key_id(parse_key(KEY_OLD))
KID_NEW = envelope_key_id(parse_key(KEY_NEW))


# ------------------------------------------------------------------
# 密钥环解析
# ------------------------------------------------------------------

def test_keyring_multi_version_last_is_active(monkeypatch):
    """多版本密钥环：按插入序解析，最后一个为活跃写入密钥。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_OLD}:{KEY_OLD},{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    ring = keyring()
    assert set(ring) == {KID_OLD, KID_NEW}
    assert active_key() == (KID_NEW, parse_key(KEY_NEW))


def test_keyring_fallback_single_key(monkeypatch):
    """密钥环未配置时回退单密钥 FEDERATION_PARAM_KEY（自动推导 key_id）。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", "")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", KEY_OLD)
    ring = keyring()
    assert ring == {KID_OLD: parse_key(KEY_OLD)}
    assert active_key()[0] == KID_OLD


def test_keyring_both_empty_plaintext_mode(monkeypatch):
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", "")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    assert keyring() == {} and active_key() == ("", b"")


def test_keyring_malformed_entry_raises(monkeypatch):
    """格式非法直接报错（静默跳过会让轮换后莫名解密失败）。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", "not-a-keyvalue-pair")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    with pytest.raises(ValueError, match="key_id:key"):
        keyring()


# ------------------------------------------------------------------
# enc2 信封：多版本密钥环下的双向互通与轮换语义
# ------------------------------------------------------------------

def test_enc2_seal_uses_active_key(monkeypatch):
    """平台封箱恒用密钥环最后一把（活跃）密钥，信封内嵌其 key_id。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_OLD}:{KEY_OLD},{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    payload = seal_envelope({"coef": [0.1], "n": 5})
    assert payload.startswith(f"enc2:{KID_NEW}:")


def test_enc2_interop_transition_ring(monkeypatch):
    """过渡期互通：新/旧密钥封箱的信封都能在双密钥环下解开（轮换不断在途任务）。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_OLD}:{KEY_OLD},{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    obj = {"coef": [0.1, -0.2], "n": 100, "local_auc": 0.75}
    assert open_envelope(agent_crypto.seal_envelope(obj, parse_key(KEY_NEW))) == obj
    assert open_envelope(agent_crypto.seal_envelope(obj, parse_key(KEY_OLD))) == obj
    # 平台封箱 → 节点（持新密钥）可解
    assert agent_crypto.open_envelope(seal_envelope(obj), parse_key(KEY_NEW)) == obj


def test_rotation_history_undecryptable(monkeypatch):
    """P1.2 验收：密钥泄露轮换后旧密钥销毁（移出密钥环）→ 历史信封永久不可解密。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_OLD}:{KEY_OLD},{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    leaked = agent_crypto.seal_envelope({"coef": [1.0], "n": 10}, parse_key(KEY_OLD))

    # 完成轮换：密钥环只保留新密钥（旧密钥销毁）
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_NEW}:{KEY_NEW}")
    with pytest.raises(ValueError, match="密钥环中"):
        open_envelope(leaked)
    # 即使攻击者拿旧密钥重放信封，key_id 不在环中也立即拒绝（不进解密分支）
    with pytest.raises(ValueError, match="密钥环中"):
        open_envelope(leaked)


def test_enc1_legacy_envelope_compat(monkeypatch):
    """enc1 旧格式过渡兼容：无 key_id，逐密钥尝试解密。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_OLD}:{KEY_OLD}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = parse_key(KEY_OLD)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, b'{"coef": [0.5], "n": 3}', None)
    enc1 = "enc1:" + base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()
    assert open_envelope(enc1) == {"coef": [0.5], "n": 3}

    # enc1 且密钥不在环中 → 明确报错
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_NEW}:{KEY_NEW}")
    with pytest.raises(ValueError, match="enc1"):
        open_envelope(enc1)


def test_plaintext_dual_mode_preserved(monkeypatch):
    """双模保持：未配密钥明文联调可用；配了密钥明文一律拒绝。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", "")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    assert open_envelope('{"coef": [0.1], "n": 1}') == {"coef": [0.1], "n": 1}
    assert seal_envelope({"a": 1}) == '{"a": 1}'  # 未配密钥封箱 = 明文

    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", KEY_OLD)
    with pytest.raises(ValueError, match="加密信封"):
        open_envelope('{"coef": [0.1], "n": 1}')


def test_malformed_enc2_rejected(monkeypatch):
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", KEY_OLD)
    with pytest.raises(ValueError, match="格式非法"):
        open_envelope("enc2:onlyonefield")


def test_agent_node_key_id_drift_detected(monkeypatch):
    """节点显式 NODE_KEY_ID 与平台密钥环对不上（配置漂移）→ 平台拒绝并可发现。"""
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    monkeypatch.setenv("FEDERATION_PARAM_KEY", KEY_NEW)
    monkeypatch.setenv("NODE_KEY_ID", "deadbeef")  # 错误的 key_id 配置
    payload = agent_crypto.seal_envelope({"n": 1})
    assert payload.startswith("enc2:deadbeef:")
    with pytest.raises(ValueError, match="密钥环中"):
        open_envelope(payload)


def test_agent_open_key_mismatch_hint(monkeypatch):
    """平台已轮换而节点未同步：节点侧报错提示更新 FEDERATION_PARAM_KEY。"""
    monkeypatch.setenv("FEDERATION_PARAM_KEY", KEY_OLD)
    monkeypatch.delenv("NODE_KEY_ID", raising=False)
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEYS", f"{KID_NEW}:{KEY_NEW}")
    monkeypatch.setattr(settings, "FEDERATION_PARAM_KEY", "")
    sealed = seal_envelope({"n": 1})  # 平台用新密钥封箱（下发给节点的全局模型）
    with pytest.raises(ValueError, match="FEDERATION_PARAM_KEY"):
        agent_crypto.open_envelope(sealed)


# ------------------------------------------------------------------
# mTLS：socketpair 真实 TLS 握手（无网络依赖）
# ------------------------------------------------------------------

def _self_signed(cn: str, ca: bool = False) -> tuple[bytes, bytes, ec.EllipticCurvePrivateKey]:
    """自签证书（CA 根）或待签 CSR 材料生成。返回 (cert_pem, key_pem, key)。"""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    builder = (x509.CertificateBuilder()
               .subject_name(name).issuer_name(name)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(datetime.now(UTC) - timedelta(days=1))
               .not_valid_after(datetime.now(UTC) + timedelta(days=365)))
    if ca:
        builder = (builder
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=False,
                                                content_commitment=False,
                                                key_encipherment=False,
                                                data_encipherment=False,
                                                key_agreement=False,
                                                key_cert_sign=True,
                                                crl_sign=True,
                                                encipher_only=False,
                                                decipher_only=False),
                                  critical=True))  # 与 gen-certs.sh 同口径：CA 必须 keyCertSign+cRLSign
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return cert.public_bytes(serialization.Encoding.PEM), key_pem, key


def _ca_signed(cn: str, ca_key, ca_cert_pem: bytes, san: list[str] | None = None) -> tuple[bytes, bytes]:
    """用联邦 CA 签发叶子证书（模拟 gen-certs.sh 签发服务端/客户端证书）。
    返回 (cert_pem, key_pem)——私钥与证书配对。"""
    key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
               .issuer_name(ca_cert.subject)
               .public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(datetime.now(UTC) - timedelta(days=1))
               .not_valid_after(datetime.now(UTC) + timedelta(days=365))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True))
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(s) for s in san]), critical=False)
    builder = (builder
               .add_extension(
                   x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                   critical=False)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                              critical=False))  # 新版 OpenSSL 要求 SKI/AKI，缺失判证书链无效
    cert = builder.sign(ca_key, hashes.SHA256())
    return (cert.public_bytes(serialization.Encoding.PEM), _pem_key_file(key))


def _pem_key_file(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _handshake(server_ctx: ssl.SSLContext, client_ctx: ssl.SSLContext):
    """socketpair 上并发推进真实 TLS 握手（客户端子线程），零网络依赖。

    返回 (服务端看到的对端证书, 首个握手异常)。异常非 None 即握手被拒。
    """
    s_srv, s_cli = socket.socketpair()
    result = {"peer": None, "server_err": None, "client_err": None}

    def client_run():
        try:
            with client_ctx.wrap_socket(s_cli, server_hostname="localhost") as c:
                c.sendall(b"ping")
                c.recv(64)  # 等服务端握手完成信号，避免 TLS1.3 下客户端提前关闭连接
        except Exception as e:  # noqa: BLE001 —— 握手失败路径
            result["client_err"] = e

    t = threading.Thread(target=client_run, daemon=True)
    t.start()
    try:
        with server_ctx.wrap_socket(s_srv, server_side=True) as srv:
            result["peer"] = srv.getpeercert()
            srv.sendall(b"pong")  # 通知客户端握手完成
            srv.recv(64)
    except Exception as e:  # noqa: BLE001 —— 服务端视角握手失败
        result["server_err"] = e
    t.join(timeout=5)
    err = result["server_err"] or result["client_err"]
    side = "server" if result["server_err"] else ("client" if result["client_err"] else "none")
    return result["peer"], (f"[{side}] {err!r}" if err else None)


@pytest.fixture
def ca_files(tmp_path) -> dict[str, Path]:
    """模拟 gen-certs.sh 产出：联邦 CA 签发服务端证书（SAN=localhost）+ 客户端证书。"""
    files = {}
    ca_cert_pem, ca_key_pem, ca_key = _self_signed("oumed-chain-federation-ca", ca=True)
    files["ca"] = tmp_path / "ca.crt"
    files["ca"].write_bytes(ca_cert_pem)

    s_cert, s_key = _ca_signed("localhost", ca_key, ca_cert_pem, san=["localhost"])
    files["server"] = tmp_path / "server.crt"
    files["server"].write_bytes(s_cert)
    files["server_key"] = tmp_path / "server.key"
    files["server_key"].write_bytes(s_key)

    c_cert, c_key = _ca_signed("fed-node-1", ca_key, ca_cert_pem)
    files["client"] = tmp_path / "client-node1.crt"
    files["client"].write_bytes(c_cert)
    files["client_key"] = tmp_path / "client-node1.key"
    files["client_key"].write_bytes(c_key)
    return files


def _server_ctx(files) -> ssl.SSLContext:
    """平台侧：TLS 服务端 + 强制客户端证书（nginx ssl_verify_client on 的等价物）。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(files["server"], files["server_key"])
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(files["ca"])  # 只信联邦 CA 签发的客户端证书
    return ctx


def test_agent_ssl_context_default_single_tls(monkeypatch):
    """默认配置（全部留空）= 系统 CA 单向 TLS，服务端证书仍强制校验。"""
    monkeypatch.setattr(agent_config, "TLS_CA_CERT", "")
    monkeypatch.setattr(agent_config, "TLS_CLIENT_CERT", "")
    monkeypatch.setattr(agent_config, "TLS_CLIENT_KEY", "")
    agent_tls.reset_cache()
    try:
        ctx = agent_tls.ssl_context()
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
    finally:
        agent_tls.reset_cache()


def test_agent_ssl_context_incomplete_config_rejected(monkeypatch):
    """只配证书不配私钥 → 明确报错（不静默降级为单向）。"""
    monkeypatch.setattr(agent_config, "TLS_CA_CERT", "")
    monkeypatch.setattr(agent_config, "TLS_CLIENT_CERT", "some.crt")
    monkeypatch.setattr(agent_config, "TLS_CLIENT_KEY", "")
    agent_tls.reset_cache()
    try:
        with pytest.raises(ValueError, match="必须同时配置"):
            agent_tls.build_ssl_context()
    finally:
        agent_tls.reset_cache()


def test_mtls_handshake_success_with_federation_cert(monkeypatch, ca_files):
    """mTLS 握手成功：节点 Agent 用联邦客户端证书 → 服务端可见对端证书主体。"""
    monkeypatch.setattr(agent_config, "TLS_CA_CERT", str(ca_files["ca"]))
    monkeypatch.setattr(agent_config, "TLS_CLIENT_CERT", str(ca_files["client"]))
    monkeypatch.setattr(agent_config, "TLS_CLIENT_KEY", str(ca_files["client_key"]))
    agent_tls.reset_cache()
    try:
        client_ctx = agent_tls.build_ssl_context()
        # Agent 上下文：联邦 CA 验证服务端 + 客户端证书双向认证（与生产配置等价）
        peer, err = _handshake(_server_ctx(ca_files), client_ctx)
        assert err is None, f"mTLS 握手不应失败: {err}"
        assert peer and "fed-node-1" in str(peer)  # 服务端确认了对端客户端证书
    finally:
        agent_tls.reset_cache()


def test_mtls_mitm_without_cert_rejected(ca_files):
    """中间人模拟（验收①）：无客户端证书 → TLS 握手直接失败（传输层拒绝）。"""
    mitm_ctx = ssl.create_default_context(cafile=str(ca_files["ca"]))  # 只验证服务端，无客户端证书
    peer, err = _handshake(_server_ctx(ca_files), mitm_ctx)
    assert err is not None, "无客户端证书的连接必须被拒绝"
    assert peer is None


def test_mtls_mitm_with_foreign_cert_rejected(ca_files, tmp_path):
    """中间人模拟（验收②）：非联邦 CA 签发的客户端证书 → 握手被拒。"""
    rogue_cert, rogue_key, _ = _self_signed("rogue-node")
    rogue_cert_path = tmp_path / "rogue.crt"
    rogue_key_path = tmp_path / "rogue.key"
    rogue_cert_path.write_bytes(rogue_cert)
    rogue_key_path.write_bytes(rogue_key)
    rogue_ctx = ssl.create_default_context(cafile=str(ca_files["ca"]))
    rogue_ctx.load_cert_chain(rogue_cert_path, rogue_key_path)  # 自带"野"证书
    peer, err = _handshake(_server_ctx(ca_files), rogue_ctx)
    assert err is not None, "非联邦 CA 签发的客户端证书必须被拒绝"
    assert peer is None


def test_mtls_server_identity_verified_by_agent(ca_files):
    """节点 Agent 侧校验服务端身份：CA 不含服务端证书时握手失败（防假平台）。"""
    agent_server_trust = ssl.create_default_context(cafile=str(ca_files["client"]))  # 信任集里没有服务端证书
    agent_server_trust.load_cert_chain(ca_files["client"], ca_files["client_key"])
    peer, err = _handshake(_server_ctx(ca_files), agent_server_trust)
    assert err is not None, "Agent 必须拒绝无法通过 CA 验证的服务端（防钓鱼平台）"
