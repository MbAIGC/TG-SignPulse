"""JWT 密钥持久化：并发首启共享同一密钥、过短密钥重建、权限收紧。"""

import os
from concurrent.futures import ThreadPoolExecutor

from backend.core.config import get_default_secret_key


def test_concurrent_first_start_shares_same_secret(tmp_path):
    """多 worker 并发首启必须拿到同一个密钥，否则互不认对方的令牌。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env = {"APP_DATA_DIR": str(data_dir)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        keys = list(pool.map(lambda _index: get_default_secret_key(env), range(8)))

    assert len(set(keys)) == 1
    assert len(keys[0]) >= 32
    assert (data_dir / ".app_secret_key").read_text(encoding="utf-8").strip() == keys[0]
    assert (data_dir / ".app_secret_key").stat().st_mode & 0o777 == 0o600


def test_short_stored_secret_is_regenerated(tmp_path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    secret_file = data_dir / ".app_secret_key"
    secret_file.write_text("short", encoding="utf-8")

    key = get_default_secret_key({"APP_DATA_DIR": str(data_dir)})

    assert key != "short"
    assert len(key) >= 32
    assert secret_file.read_text(encoding="utf-8").strip() == key
    assert "过短" in capsys.readouterr().out


def test_existing_secret_keeps_value_and_tightens_permissions(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    secret_file = data_dir / ".app_secret_key"
    existing = "k" * 48
    secret_file.write_text(existing, encoding="utf-8")
    os.chmod(secret_file, 0o644)

    key = get_default_secret_key({"APP_DATA_DIR": str(data_dir)})

    assert key == existing
    assert secret_file.stat().st_mode & 0o777 == 0o600
