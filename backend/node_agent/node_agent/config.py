"""节点 Agent 配置：全部来自环境变量（Docker/部署脚本注入，无配置文件）。"""

import os

# 平台 coordinator 地址（如 https://fed.oumed.cn）
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://localhost:8100")

# 节点身份凭证（平台管理端注册节点后一次性下发；NODE_TOKEN 仅存本机环境）
NODE_ID = os.environ.get("NODE_ID", "")
NODE_TOKEN = os.environ.get("NODE_TOKEN", "")

# 本院数据连接凭证：CSV 文件路径（原始数据不出节点，Agent 只读该文件）
DATA_PATH = os.environ.get("DATA_PATH", "")

# 参数上行加密密钥（32 字节 base64/hex/口令；与平台 FEDERATION_PARAM_KEY 一致；
# 留空 = 明文兼容联调模式，生产必须配置。密钥轮换后更新为新密钥，
# NODE_KEY_ID 可显式指定密钥版本号，留空自动按密钥推导）
FEDERATION_PARAM_KEY = os.environ.get("FEDERATION_PARAM_KEY", "")
NODE_KEY_ID = os.environ.get("NODE_KEY_ID", "")

# P1.2 传输安全（mTLS 双向认证）：全部留空 = 普通 TLS（系统 CA 验证服务端）。
# - TLS_CA_CERT：平台自建 CA 证书（签发服务端+客户端证书的根），用于验证服务端；
# - TLS_CLIENT_CERT / TLS_CLIENT_KEY：节点客户端证书（gen-certs.sh 签发），
#   平台 nginx 反代 ssl_verify_client 校验，中间人无证书无法握手。
TLS_CA_CERT = os.environ.get("TLS_CA_CERT", "")
TLS_CLIENT_CERT = os.environ.get("TLS_CLIENT_CERT", "")
TLS_CLIENT_KEY = os.environ.get("TLS_CLIENT_KEY", "")

# 参与的任务 ID（留空则自动发现本节点被指派的进行中任务）
NODE_JOB_ID = os.environ.get("NODE_JOB_ID", "")

# 轮询间隔（秒）：拉取任务状态/全局模型的频率
POLL_INTERVAL = float(os.environ.get("NODE_POLL_INTERVAL", "3"))

# 本地训练测试集占比（节点自留验证集，用于上报聚合 AUC 指标）
TEST_RATIO = float(os.environ.get("NODE_TEST_RATIO", "0.25"))

# DP 随机种子基值（各节点不同 → 噪声互不相同）
DP_SEED = int(os.environ.get("NODE_DP_SEED", "7"))


def validate() -> list[str]:
    """启动自检：返回缺失的必填配置项列表。"""
    missing = []
    if not NODE_ID:
        missing.append("NODE_ID")
    if not NODE_TOKEN:
        missing.append("NODE_TOKEN")
    if not DATA_PATH:
        missing.append("DATA_PATH")
    return missing
