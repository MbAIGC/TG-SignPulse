from __future__ import annotations

import contextlib
import os
import secrets
import tempfile
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from backend.utils.storage import get_initial_data_dir, get_writable_base_dir

try:  # pragma: no cover - Windows 无 fcntl，退化为仅原子写
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

# 已有密钥文件短于该长度时视为不可信，重新生成（正常生成的密钥为 64 字符）
_MIN_STORED_SECRET_LENGTH = 16


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key:
                values[key] = value
    except OSError:
        return {}
    return values


def _merged_env() -> dict[str, str]:
    return {**_load_env_file(Path(".env")), **os.environ}


def _read_env(
    env: Mapping[str, str],
    *names: str,
    default: str | None = None,
) -> str | None:
    for name in names:
        value = str(env.get(name, "")).strip()
        if value:
            return value
    return default


def _read_int_env(env: Mapping[str, str], *names: str, default: int) -> int:
    raw = _read_env(env, *names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _read_path_env(env: Mapping[str, str], *names: str) -> Path | None:
    raw = _read_env(env, *names)
    if raw is None:
        return None
    return Path(raw).expanduser()


# 已知的弱 JWT 密钥（占位/示例值），显式配置时直接拒绝启动
_WEAK_SECRET_KEYS = {
    "your_secret_key",
    "your_secret_key_here",
    "secret",
    "changeme",
    "change_me",
    "password",
    "default",
    "123456",
    "12345678",
}

# 显式配置的密钥最小长度；自动生成值不受此限制
_SECRET_KEY_MIN_LENGTH = 32


def _is_weak_secret_key(value: str) -> bool:
    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in _WEAK_SECRET_KEYS:
        return True
    if len(stripped) < _SECRET_KEY_MIN_LENGTH:
        return True
    return False


def _read_secret_file(secret_file: Path) -> str:
    """读取持久化密钥；过短视为不可信并重新生成，同时把权限收紧到 0600。"""
    try:
        value = secret_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not value:
        return ""
    if len(value) < _MIN_STORED_SECRET_LENGTH:
        print(
            f"[config] 警告：{secret_file} 中保存的 JWT 密钥过短"
            f"（少于 {_MIN_STORED_SECRET_LENGTH} 字符），将重新生成持久密钥，"
            "已签发的登录令牌会失效。",
            flush=True,
        )
        return ""
    try:
        os.chmod(secret_file, 0o600)
    except OSError:
        pass
    return value


def _write_secret_file_locked(secret_file: Path, generated: str) -> None:
    """在文件锁内原子写入密钥，避免并发首启各自生成不同的密钥。

    锁内会重读一次：其他进程若已写入，则直接复用（由调用方重新读取返回）。
    """
    lock_handle = None
    try:
        if fcntl is not None:
            lock_handle = open(f"{secret_file}.lock", "a+")
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)

        if _read_secret_file(secret_file):
            return

        fd, tmp_name = tempfile.mkstemp(
            dir=str(secret_file.parent), prefix=".app_secret_key.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(generated)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, secret_file)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    finally:
        if lock_handle is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


def get_default_secret_key(env: Mapping[str, str] | None = None) -> str:
    env_map = env or os.environ
    env_secret = _read_env(env_map, "APP_SECRET_KEY")
    if env_secret:
        if _is_weak_secret_key(env_secret):
            raise ValueError(
                "APP_SECRET_KEY 过弱或被列入已知弱值：请设置至少 32 字符的高强度随机密钥"
                "（例如 `openssl rand -hex 32` 的输出），否则拒绝启动。"
            )
        return env_secret

    data_dir = _read_path_env(env_map, "APP_DATA_DIR")
    base_dir = data_dir or get_initial_data_dir()
    if str(base_dir) == "/data":
        base_dir = get_writable_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    secret_file = base_dir / ".app_secret_key"

    existing = _read_secret_file(secret_file)
    if existing:
        return existing

    generated = secrets.token_urlsafe(48)
    try:
        # 加锁 + 锁内重读：多 worker 并发首启必须共享同一个密钥，
        # 否则 A 进程签发的 JWT 在 B 进程校验会失败。
        _write_secret_file_locked(secret_file, generated)
        existing = _read_secret_file(secret_file)
        if existing:
            return existing
    except OSError:
        # 生产模式下密钥未持久化 = 重启后所有旧 JWT 失效，必须拒绝启动；
        # 开发模式下则降级为可用的随机值但记录明显告警。
        env_prod = str(env_map.get("APP_ENV", "")).strip().lower()
        is_production = env_prod in ("production", "prod")
        if is_production:
            raise RuntimeError(
                "无法把自动生成的 JWT 密钥持久化到 %s：请确保该路径可写，"
                "或显式设置 APP_SECRET_KEY（openssl rand -hex 32），否则重启后旧令牌将全部失效。"
                % secret_file
            )
        print(
            "[config] 警告：JWT 密钥无法写入 %s，当前进程可用，但重启后密钥将变化、旧令牌失效。"
            % secret_file,
            flush=True,
        )
    return generated


class Settings(BaseModel):
    app_name: str = "tg-signer-panel"
    host: str = "127.0.0.1"
    port: int = 8080
    cors_allow_origins_raw: str = (
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:8080,http://localhost:8080"
    )
    secret_key: str = Field(default_factory=get_default_secret_key)
    access_token_expire_hours: int = 12
    timezone: str = "Asia/Hong_Kong"
    frontend_dev_url: str = "http://127.0.0.1:5173"
    data_dir: Path = Field(default_factory=get_initial_data_dir)
    db_path: Path | None = None
    signer_workdir: Path | None = None
    session_dir: Path | None = None
    logs_dir: Path | None = None
    web_dir: Path | None = None

    @classmethod
    def from_environment(cls) -> Settings:
        env = _merged_env()
        return cls(
            app_name=_read_env(
                env, "APP_APP_NAME", "APP_NAME", default="tg-signer-panel"
            ),
            host=_read_env(env, "APP_HOST", default="127.0.0.1"),
            port=_read_int_env(env, "APP_PORT", "PORT", default=8080),
            cors_allow_origins_raw=_read_env(
                env,
                "APP_CORS_ALLOW_ORIGINS",
                default=(
                    "http://127.0.0.1:5173,http://localhost:5173,"
                    "http://127.0.0.1:8080,http://localhost:8080"
                ),
            ),
            secret_key=get_default_secret_key(env),
            access_token_expire_hours=_read_int_env(
                env,
                "APP_ACCESS_TOKEN_EXPIRE_HOURS",
                default=12,
            ),
            timezone=_read_env(env, "TZ", "APP_TIMEZONE", default="Asia/Hong_Kong"),
            frontend_dev_url=_read_env(
                env,
                "FRONTEND_DEV_SERVER_URL",
                default="http://127.0.0.1:5173",
            ),
            data_dir=_read_path_env(env, "APP_DATA_DIR") or get_initial_data_dir(),
            db_path=_read_path_env(env, "APP_DB_PATH"),
            signer_workdir=_read_path_env(env, "APP_SIGNER_WORKDIR"),
            session_dir=_read_path_env(env, "APP_SESSION_DIR"),
            logs_dir=_read_path_env(env, "APP_LOGS_DIR"),
            web_dir=_read_path_env(env, "APP_WEB_DIR"),
        )

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.resolve_db_path()}?check_same_thread=False"

    def resolve_db_path(self) -> Path:
        return self.db_path or self.resolve_base_dir() / "db.sqlite"

    def resolve_workdir(self) -> Path:
        return self.signer_workdir or self.resolve_base_dir() / ".signer"

    def resolve_session_dir(self) -> Path:
        return self.session_dir or self.resolve_base_dir() / "sessions"

    def resolve_logs_dir(self) -> Path:
        return self.logs_dir or self.resolve_base_dir() / "logs"

    def resolve_base_dir(self) -> Path:
        if self.data_dir and str(self.data_dir) != "/data":
            return self.data_dir
        return get_writable_base_dir()

    def resolve_web_dir(self) -> Path:
        if self.web_dir is not None:
            return self.web_dir
        # 默认指向前端构建产物: <repo>/frontend/dist
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @property
    def cors_allow_origins(self) -> list[str]:
        origins = [
            item.strip()
            for item in str(self.cors_allow_origins_raw or "").split(",")
            if item.strip()
        ]
        return origins or ["http://127.0.0.1:5173", "http://localhost:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
