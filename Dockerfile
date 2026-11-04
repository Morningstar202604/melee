FROM python:3.13-slim

# ffmpeg 用于高光渲染；其余为 Pillow 的运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# 构建期可换镜像源（国内网络下 pypi.org 常超时）
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt

COPY services ./services
COPY packages ./packages
COPY apps ./apps
COPY scripts ./scripts
COPY pyproject.toml NOTICE ./

ENV MELEE_DB=/data/match.db \
    MELEE_HOST=0.0.0.0 \
    MELEE_PORT=8099 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data && useradd -m -u 10001 melee && chown -R melee /data /app
USER melee

EXPOSE 8099

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8099/health || exit 1

CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8099"]
