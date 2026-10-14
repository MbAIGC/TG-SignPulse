# syntax=docker/dockerfile:1

# ---------- 1. 前端构建 ----------
FROM node:22-alpine AS frontend
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
# --include=dev：vue-tsc/vite 均为 dev 依赖；显式安装避免 npm omit=dev 配置
# 导致构建命令缺失（与 CI lint-test-build.yml 保持一致）。
RUN npm ci --include=dev --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ---------- 2. 后端依赖 ----------
FROM python:3.11-slim AS backend-deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml README.md LICENSE ./
# build-essential 只在依赖阶段使用（运行镜像不包含）：tgcrypto 需要编译，
# 多架构构建（arm64/QEMU）也依赖它。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install tgcrypto

COPY tg_signer/ tg_signer/
RUN /opt/venv/bin/pip install .

# ---------- 3. 运行镜像 ----------
FROM python:3.11-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_DATA_DIR=/data

# 构建信息（供版本展示与排查）
ARG BUILD_DATE=""
ARG BUILD_SHA=""
ENV BUILD_DATE="${BUILD_DATE}" \
    BUILD_SHA="${BUILD_SHA}"

# tzdata: 支持 TZ 环境变量；gosu: entrypoint 降权运行
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata gosu \
    && rm -rf /var/lib/apt/lists/*

# 非 root 运行用户；entrypoint 会按 /data 属主自适应
ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd -r -g ${APP_GID} app \
    && useradd -r -u ${APP_UID} -g app -d /app -s /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown -R app:app /data

WORKDIR /app
COPY --from=backend-deps /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY --from=frontend /build/frontend/dist /app/frontend/dist

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\", os.getenv(\"APP_PORT\", \"8080\"))}/healthz', timeout=3)"

# Do not trust forwarded client headers by default. Configure a trusted
# reverse proxy explicitly if the deployment needs the original client IP.
ENTRYPOINT ["/entrypoint.sh"]
