"""静态前端托管加固：未知 /api/* 不落 SPA、路径判定不用字符串前缀、缓存头正确。"""

import asyncio
import os
from pathlib import Path

import pytest

import backend.main as main_mod
from backend.main import _html_response, _ImmutableStaticFiles, serve_spa


@pytest.fixture
def web_tree(tmp_path):
    """构造 dist 与其同前缀兄弟目录 distx，用于验证前缀比较漏洞。"""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>index</html>", encoding="utf-8")
    (dist / "about.html").write_text("<html>about</html>", encoding="utf-8")

    sibling = tmp_path / "distx"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")
    return dist, sibling


def test_unknown_api_path_returns_404_instead_of_spa(monkeypatch, web_tree):
    dist, _sibling = web_tree
    monkeypatch.setattr(main_mod, "web_dir", dist)
    # 开发模式下也不能把未知 API 重定向到前端 dev server
    monkeypatch.setattr(
        main_mod.settings, "frontend_dev_url", "http://127.0.0.1:5173", raising=False
    )

    for path in ("api", "api/unknown", "api/accounts/nope"):
        response = asyncio.run(serve_spa(path))
        assert response.status_code == 404, path


def test_sibling_directory_with_same_prefix_is_not_served(monkeypatch, web_tree):
    dist, _sibling = web_tree
    monkeypatch.setattr(main_mod, "web_dir", dist)
    monkeypatch.setattr(main_mod.settings, "frontend_dev_url", "", raising=False)

    response = asyncio.run(serve_spa("../distx/secret.txt"))

    # 不能返回兄弟目录内容：要么落到 index.html，要么 404
    body = b""
    if hasattr(response, "body"):
        body = response.body
    assert b"TOP-SECRET" not in body


def test_spa_fallback_serves_html_with_no_cache(monkeypatch, web_tree):
    dist, _sibling = web_tree
    monkeypatch.setattr(main_mod, "web_dir", dist)

    response = asyncio.run(serve_spa("dashboard"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_static_html_file_gets_no_cache(monkeypatch, web_tree):
    dist, _sibling = web_tree
    monkeypatch.setattr(main_mod, "web_dir", dist)

    response = asyncio.run(serve_spa("about.html"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_hashed_assets_are_cached_immutably(tmp_path):
    (tmp_path / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    stat_result = os.stat(tmp_path / "app-abc123.js")
    scope = {"type": "http", "method": "GET", "headers": []}

    static = _ImmutableStaticFiles(directory=str(tmp_path))
    response = static.file_response(
        Path(tmp_path / "app-abc123.js"), stat_result, scope
    )

    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_html_response_helper_sets_no_cache(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html></html>", encoding="utf-8")

    response = _html_response(index)

    assert response.headers["cache-control"] == "no-cache"
