#!/bin/sh
# 容器入口：按 /data 挂载卷的属主自适应运行身份，避免 chmod 777。
#
# 环境变量：
#   PORT / APP_PORT              监听端口（默认 8080）
#   APP_UID / APP_GID            默认运行身份（默认 10001）
#   APP_AUTO_FIX_DATA_PERMS=0    关闭 /data 属主与写位修复
set -eu

PORT_VALUE="${PORT:-${APP_PORT:-8080}}"
AUTO_FIX_PERMS="${APP_AUTO_FIX_DATA_PERMS:-1}"
TARGET_UID="${APP_UID:-10001}"
TARGET_GID="${APP_GID:-10001}"

# 挂载卷属主优先：宿主机上以普通用户创建的 ./data 也能直接跑
if [ -d /data ]; then
    DATA_UID="$(stat -c '%u' /data 2>/dev/null || true)"
    DATA_GID="$(stat -c '%g' /data 2>/dev/null || true)"
    if [ -n "${DATA_UID}" ] && [ -n "${DATA_GID}" ]; then
        TARGET_UID="${DATA_UID}"
        TARGET_GID="${DATA_GID}"
    fi
fi

start_app() {
    exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT_VALUE}"
}

# 非 root 启动（如 compose 指定了 user:）：直接运行
if [ "$(id -u)" -ne 0 ]; then
    start_app
fi

if [ "${AUTO_FIX_PERMS}" != "0" ] && [ -d /data ]; then
    echo "INFO: 正在将 /data 属主调整为 ${TARGET_UID}:${TARGET_GID} ..."
    mkdir -p /data/sessions /data/logs 2>/dev/null || true
    for path in /data /data/sessions /data/logs; do
        if [ -e "${path}" ]; then
            chown -R "${TARGET_UID}:${TARGET_GID}" "${path}" 2>/dev/null || true
            chmod -R u+rwX "${path}" 2>/dev/null || true
        fi
    done
fi

# 挂载卷本身就是 root 所有：保持 root 运行以确保可写
if [ "${TARGET_UID}" = "0" ] || [ "${TARGET_GID}" = "0" ]; then
    start_app
fi

# 只读根文件系统下 HOME 可能不可写，回退到 /tmp
if [ -n "${HOME:-}" ] && [ ! -w "${HOME}" ]; then
    HOME=/tmp
    export HOME
fi

if command -v gosu >/dev/null 2>&1; then
    exec gosu "${TARGET_UID}:${TARGET_GID}" \
        uvicorn backend.main:app --host 0.0.0.0 --port "${PORT_VALUE}"
fi

if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid="${TARGET_UID}" --regid="${TARGET_GID}" --init-groups \
        uvicorn backend.main:app --host 0.0.0.0 --port "${PORT_VALUE}"
fi

echo "WARN: 未找到 gosu/setpriv，将以 root 运行" >&2
start_app
