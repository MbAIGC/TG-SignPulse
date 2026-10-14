"""版本信息与更新检查（只读）。

只做“有没有新版本”的检查，不提供页面内一键更新：一键更新需要把
Docker socket 挂进容器，等于把宿主机 root 权限交给面板登录态。
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from tg_signer import __version__ as app_version

logger = logging.getLogger("backend.update_check")

# 默认检查本仓库的发布标签；可通过 APP_UPDATE_REPO 覆盖（owner/repo）
DEFAULT_UPDATE_REPO = "MbAIGC/TG-SignPulse"
CHECK_TTL_SECONDS = 6 * 60 * 60

_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

_lock = threading.Lock()
_cache: dict[str, Any] = {"checked_at": 0.0, "payload": None}


def build_info() -> dict[str, str]:
    """当前版本与构建信息（构建期通过 --build-arg 注入）。"""
    return {
        "version": app_version,
        "build_sha": str(os.getenv("BUILD_SHA") or "").strip(),
        "built_at": str(os.getenv("BUILD_DATE") or "").strip(),
    }


def _update_check_disabled() -> bool:
    raw = str(os.getenv("APP_UPDATE_CHECK", "1")).strip().lower()
    return raw in ("0", "false", "no", "off")


def _parse_version(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.match((value or "").strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _fetch_latest_release(repo: str) -> dict[str, Any]:
    """读取 GitHub 最新正式发布标签；只读、不认证、超时 10s。"""
    headers = {"Accept": "application/vnd.github+json"}
    token = str(os.getenv("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=10.0) as client:
        response = client.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise ValueError("发布信息缺少 tag_name")
    return {
        "tag": tag,
        "html_url": str(data.get("html_url") or ""),
        "published_at": str(data.get("published_at") or ""),
        "name": str(data.get("name") or tag),
        "body": str(data.get("body") or "")[:2000],
    }


def get_update_status(force: bool = False) -> dict[str, Any]:
    """检查是否有新版本；结果缓存 6 小时（失败不写缓存）。"""
    info = build_info()
    current = info["version"]
    payload: dict[str, Any] = {
        **info,
        "repo": str(os.getenv("APP_UPDATE_REPO") or DEFAULT_UPDATE_REPO).strip(),
        "latest": "",
        "update_available": False,
        "release_url": "",
        "published_at": "",
        "release_name": "",
        "release_notes": "",
        "checked_at": "",
        "disabled": False,
        "error": "",
    }

    if _update_check_disabled():
        payload["disabled"] = True
        payload["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return payload

    now = time.time()
    with _lock:
        cached = _cache.get("payload")
        if (
            not force
            and cached is not None
            and now - float(_cache.get("checked_at") or 0) < CHECK_TTL_SECONDS
        ):
            return dict(cached)

    try:
        release = _fetch_latest_release(payload["repo"])
    except Exception as exc:
        logger.warning("检查更新失败（%s）: %s", payload["repo"], exc)
        payload["error"] = str(exc)
        payload["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        return payload

    latest_tag = release["tag"]
    latest_version = _parse_version(latest_tag)
    current_version = _parse_version(current)
    payload.update(
        {
            "latest": latest_tag.lstrip("v") if latest_version else latest_tag,
            "release_url": release["html_url"],
            "published_at": release["published_at"],
            "release_name": release["name"],
            "release_notes": release["body"],
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    if latest_version is not None and current_version is not None:
        payload["update_available"] = latest_version > current_version

    with _lock:
        _cache["checked_at"] = time.time()
        _cache["payload"] = dict(payload)
    return payload
