# 瓯医数链 · 医院侧联邦节点 Agent（P1.1：Docker 单容器，部署在医院内网）
# 构建节点镜像：  docker build -t oumed-node-agent .
# 运行示例见 docs/联邦节点部署与演练方案.md
FROM python:3.12-slim

WORKDIR /agent
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY node_agent ./node_agent

# 数据目录挂载点（医院本院数据 CSV，只读挂载）
VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "node_agent.main"]
