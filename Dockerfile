# Zai Agent Dockerfile
# 基于 Python 3.12 官方镜像

FROM python:3.12-slim

# 防止中文乱码
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONIOENCODING=utf-8

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# 设置工作目录
WORKDIR /app

# 复制依赖文件
COPY pyproject.toml uv.lock ./

# 安装依赖（使用镜像层缓存）
RUN uv sync --frozen --no-dev

# 复制源码
COPY src/ ./src/
COPY prompts/ ./prompts/

# 安装工具命令
RUN uv tool install .

# 默认工作目录
WORKDIR /workspace

# 默认命令
ENTRYPOINT ["zai"]
CMD ["--help"]
