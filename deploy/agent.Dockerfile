# aite 执行机 agent 镜像（模块 G：拉取式纳管）
# 构建（仓库根目录）：
#   docker build -f deploy/agent.Dockerfile -t aite-agent:latest .
#   docker build -f deploy/agent.Dockerfile --target with-perf -t aite-agent:perf .
# 运行（执行机上）：
#   docker run -d --name agent-1 aite-agent:latest \
#     aite agent serve --master http://<master-host>:8300 --name node-1
FROM python:3.10-slim AS base

WORKDIR /app
COPY pyproject.toml README.md ./
COPY aite ./aite
RUN pip install --no-cache-dir ".[locust]"

CMD ["aite", "agent", "serve", "--help"]

# perf 层：evalscope 依赖重（含 torch 链），压测执行机用此 target
FROM base AS with-perf
RUN pip install --no-cache-dir evalscope[perf]
