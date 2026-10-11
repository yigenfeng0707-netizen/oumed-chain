"""瓯医数链 · 医院侧联邦学习节点 Agent（P1.1 联邦真实部署）。

独立可安装包：不依赖平台 app 包，仅依赖 numpy + cryptography，
Docker 单容器部署在医院内网，只持有本院数据连接凭证（CSV 路径 + 平台节点 token）。

核心承诺（验收口径）：
- 原始数据零传输：训练全程在本院机器内存中完成，上行只有模型参数/聚合统计；
- 加密上行：参数以 AES-256-GCM 信封封装（P1.2 起线格式 enc2:key_id:nonce:ct，
  支持多版本密钥环轮换；旧 enc1 解密兼容）；
- 传输安全：TLS/mTLS 双向认证可选（tls.py，配置 TLS_CA_CERT/TLS_CLIENT_CERT）；
- 协议对等：与平台 coordinator（/api/federation/nodes/*）逐轮握手完成联邦训练。
"""

__version__ = "1.1.0"
