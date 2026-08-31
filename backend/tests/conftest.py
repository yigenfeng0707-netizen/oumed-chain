"""pytest 全局夹具：测试环境关闭定时锚定调度。

避免 TestClient 触发 lifespan 时向真实 TSA（freetsa.org）发起网络请求。
生产默认 24h 由 config.py 提供；此处仅测试态覆盖。
"""

import os

os.environ.setdefault("CHAIN_ANCHOR_INTERVAL_HOURS", "0")
