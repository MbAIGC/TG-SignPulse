# tg-signer 0.9.0 同步记录（2026-08-30）

> 结论：TG-SignPulse 作为 tg-signer 的 **Web 端**，库（`tg_signer/`）整体跟进
> [amchii/tg-signer](https://github.com/amchii/tg-signer) 0.9.0（分叉点 0.8.4 /
> b153c59，46 commits 领先），面板（`backend/` + `frontend/`）只做适配。
> 冲突裁决原则：**库功能来 0.9.0，面板依赖保我方**。
> 结果：166 个测试全部通过，`tg_signer/` ruff 0.14.6 全绿。

## 1. 同步范围

来自 0.9.0（整体接入）：

- **RouteKey 消息路由**：`(chat_id, message_thread_id)` 作为统一路由键。
  - `get_route_key` / `resolve_chat_route_key`（`@username` → 数字 id，
    缓存于 `context.resolved_route_keys`）/ `get_runtime_route_key`
  - `_on_message` 按 route_key 路由，话题未命中时回退 `(chat_id, None)`
  - `wait_for` 的已消费消息占位（`chat_messages[route_key][id] = None`）
- **自动化规则引擎**：`tg_signer/automation/`（engine/handlers/models）、
  CLI `automation` 子命令、`docs/automation_*.md`；自动化功能优先放该子系统
- **SQLite 签到记录**：`sign_record_store.py`（`data.sqlite3` 为主存储），
  旧 `sign_record.json` 自动导入兼容；新 CLI `list-sign-records` /
  `migrate-sign-records`
- **_kurigram 补丁层**：`tg_signer/_kurigram/`，修复上游 kurigram 行为；
  内存会话改 `SQLiteStorage(in_memory=True)`（kurigram 2.2.19+ 已移除
  `MemoryStorage`，导入处做了容错）
- **CLI**：`list-folders` / `version`；`run --folder`（按 Telegram 文件夹
  过滤）；`run_once` / `send_text` 别名；`signer.py / monitor.py /
  automation.py` 均取 0.9.0
- **依赖（pyproject.toml）**：`kurigram>=2.2.19,<2.3.0`、pydantic v2
  （0.9.0 已自迁：`field_validator` / `ConfigDict`）、click / openai /
  croniter / json_repair / httpx；可选 extras：`yaml`（pyyaml）、`gui`
  （nicegui，面板不使用）
- **配置模型**：`SignConfigV3` 等配置与 `tgov2/v3` 迁移逻辑取 0.9.0；
  `SupportAction` 1–5 同 0.9.0
- **测试**：恢复 `test_core.py`、`test_match_config.py`、
  `test_sign_config_v2_to_v3.py`、`test_cli_folders.py`（均按 0.9.0）+ 自动
  化测试；客户端生命周期用例按我方 connect 语义重写

保留我方（面板依赖，合并时 keep-ours / 局部混合）：

- **客户端生命周期 `__aenter__`/`__aexit__`**：connect + 显式 unauthorized
  检测 + DB 锁重试 + WAL + 引用计数（`__aexit__` 清 `pop refs/instances/
  locks` + `trim_memory`）；对启动期 `ConnectionError` **回滚并抛出**
  （0.9.0 是 start-based 并吞掉 ConnectionError —— 面板需要无效会话显式报错）
- `close_client_by_name` / `close_all_clients`（尊重引用计数）
- `request_callback_answer` 重试版
- `SupportAction` 6/7/8 与三个 AI 动作类
  （`ReplyByImageRecognitionAction` / `ClickButtonByCalculationProblemAction`
  / `KeywordNotifyAction`）及 `ai_prompt` 字段（0.9.0 的
  `ReplyByCalculationProblemAction` 无该字段，已补回）、`desc` 属性
- `utils.atomic_write_json/text`（int/str 兼容实现）
- 登录防御性重连、`wait_for` 的后点击跟随 / 终态判定 / 历史消息回退
  （键位改为 route_key）

## 2. 关键行为裁决（冲突合并决策）

| 区域 | 取 | 理由/说明 |
| --- | --- | --- |
| `context` 字段（sign_chats/chat_messages/waiter） | 0.9.0 RouteKey + 我方面板字段 | `stop_after_current_action`、`current_action_*`、`logged_action_message_markers`、`last_callback_answer` 等面板运行时字段保留 |
| `wait_for` 主循环 | 我方 260 行可靠性逻辑，键位 RouteKey | 简单分发优先：每个候选消息先走 `_click_keyboard_by_text(action, message)`（2 参，0.9.0 语义），成功即消费返回；失败再走 `_click_keyboard_by_text_result` 增强路径（before_click 快照 + 后点击跟随 + 终态判定 + 历史回退） |
| `_on_message` 路由 | 0.9.0 | 按 route_key 精确匹配 + 话题回退；删除原 topic_matched 尾段与 200 条缓存上限 |
| `normal_run` 生命周期 | 我方手动 start/stop（getattr 防御式） | 兼容 DummyApp 测试桩；`async with` 包装在 `run()` 外层已处理引用 |
| 签到记录写入 | 0.9.0（`persist_sign_record` → SQLite） | 与 AGENTS.md "SQLite 为主存储" 一致；旧 JSON 自动导入 |
| `run()` 聊天循环 | 0.9.0 逐 chat `resolve_chat_route_key` + try/except RPCError | `@用户名` 解析失败仅跳过该 chat，不影响其余 |
| `__init__` 版本号 | 0.9.0（`__version__ = "0.9.0"`） | |
| pydantic 导入 | 0.9.0 纯净版 | v2 专有 |

## 3. 依赖与运行环境变化

- kurigram 升级到 `2.2.25`（约束 `>=2.2.19,<2.3.0`）：移除 `MemoryStorage`；
  内存会话 = `SQLiteStorage(in_memory=True)`；`ChatType` 枚举含 FORUM/DIRECT
- pydantic v2（0.9.0 已迁移；面板此前已是 v2）
- `python3.11` 无系统 pip → 用 `.venv`（`virtualenv.pyz`）；pip 需
  `export PIP_CACHE_DIR="$PWD/.venv/pip-cache" TMPDIR="$PWD/.venv/tmp"`
- pytest 配置含 `-x`，迭代时用
  `-o addopts="-W=ignore::DeprecationWarning:pyrogram.utils"` 覆盖

## 4. 已知兼容性差异（文档化）

- **启动期错误语义**：我方 `__aenter__` 对 `ConnectionError` 抛错，0.9.0
  吞掉 —— 面板依赖显式报错，勿改回
- **`get_me` 调用数**：我方 `__aenter__` 握手额外调一次 `get_me`，因此
  "单次登录引导" 计数为 2（握手 + login），并发共享测试按此断言
- **CLI 默认不拉取对话**（`--num-of-dialogs` 默认 0）——自 2026-08-29 起

## 5. 遗留项（未处理，明确记录）

- `backend/` 存量 ruff E402（模块级 import 不在文件顶部，属面板历史风格债，
  非本次合并引入；`ruff check tg_signer/` 全绿）
- `webui/`（0.9.0 的 NiceGUI WebUI）未取 —— TG-SignPulse 面板形态不同；
  生产镜像统一使用仓库根目录 `Dockerfile`，由 `docker-build-push` 工作流构建
- 0.9.0 `trim_memory` 未补回 CLI（库里保留）
- `frontend/` 未改动（面板仅通过 backend API 交互，API 签名未变）

## 6. 验证

- `pytest tests`（`-o addopts=...` 覆盖 `-x`）：**166 passed**
- `ruff check tg_signer/`：**0 errors**（0.14.6）；`ruff format` 已应用
- `python -m tg_signer --help`：CLI 正常（含 0.9.0 新命令）
- backend 全模块导入冒烟通过