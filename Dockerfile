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

WORKDIR /app
COPY --from=backend-deps /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY --from=frontend /build/frontend/dist /app/frontend/dist

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
