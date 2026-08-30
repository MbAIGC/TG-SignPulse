# 上游同步裁决表（Upstream Sync Decision Table）

> 用途：每次同步上游（当前基线 `amchii/tg-signer 0.9.0`，后续 0.10 等）前，
> 对照本表逐条裁决「上游语义 vs 面板差异」，减少 `tg_signer/core.py` / `backend/` 冲突。
> 维护原则：**库功能来上游，面板依赖保我方**（见 `doc/sync-amchii-0.9-2026-08-30.md`）。
> 本表由 `doc/review-upstream-0.9-optimization.md` §6.3 模板扩展而来，并吸收
> `sync-upstream` 分支审查（akasls 吸收）发现的 P1/P2 风险点。

## 1. 模块裁决表

| 模块 | 上游语义 | 面板差异 | 裁决 | 回归测试 |
|---|---|---|---|---|
| client lifecycle | `async with` + 引用计数 | 面板需显式异常释放、`close_client_by_name` 双 key | 保留本地增强 | 连接、重连、引用计数、`::memory` 双 key 关闭 |
| route key | `(chat_id, thread_id)` | 面板日志扩展 `last_target_message` | 以 0.9 为准 | 多 chat、topic、`@username` 解析 |
| sign records | SQLite（`SignRecordStore`） | 旧 history JSON 兼容读 | SQLite 为主 | 迁移、查询一致性、API/CLI 一致 |
| scheduler | 库内运行 | 面板有子进程任务（`async_run_task_cli`） | 逐步统一，短期保留但必须跨进程锁 | 账号互斥、并发只创建一个 run |
| config migration | V1/V2/V3 | 面板字段扩展 | 递归兼容链（V3→V2→V1） | V1→V2→V3、写回、幂等 |
| account lock | 进程内 | 面板需跨进程互斥 | `AccountLock`（asyncio.Lock 子类 + fcntl 文件锁） | 见 `tests/test_account_locks.py` |
| task run status | 无 | 面板需 run_id + 状态机 | 面板侧 `_run_statuses` 内存+磁盘持久化 | 见 `tests/test_sign_tasks_run_status.py` |

## 2. 待裁决风险点（同步前需定夺；当前 HEAD 均存在）

> 以下来自 `sync-upstream`（akasls 吸收）审查结论。**P1 建议在下次同步 0.10 前处理**，
> P2 记录备案，避免升级时被上游差异掩盖。

| # | 级别 | 模块 | 风险点 | 现状（HEAD） | 建议裁决 |
|---|---|---|---|---|---|
| 1 | P1 | `backend/main.py` `serve_spa` | SPA 路由用 `str(file_path).startswith(str(resolved_web))` 前缀检查，`..` 可穿越到同前缀兄弟目录 | 存在（L197/L201） | 改用 `Path.is_relative_to()` 或 `os.path.commonpath` 等价检查；补回归测试 |
| 2 | P1 | `tg_signer/core.py` `get_dialogs` | `app.get_dialogs(limit=100)` 硬限制对话数（L1228）；L1990 无 limit | 存在 | 面板侧保留 limit 参数化，不在库层硬编码 |
| 3 | P1 | `backend/core/rate_limit.py` | 无效 IP 回退 `"unknown"` 使所有无头/无效 IP 共享同一限流桶，可被滥用 | 存在（L109+） | 无效 IP 单独命名空间或拒绝，避免共享桶 |
| 4 | P1 | `backend/api/routes/auth.py` `reset_totp` | 未配置 `APP_EMERGENCY_RESET_KEY` 时 API 一律 403 | 存在（L194-199） | 属有意的安全设计，**保留**；文档注明 CLI 兜底 `reset-totp` |
| 5 | P1 | `backend/utils/tg_session.py` | 账号级并发锁仍是单进程 `threading.Lock` | 外层执行互斥已闭环：DB task（3.1）与 SignTask 均持有 `AccountLock`（跨进程文件锁）；`tg_session` 内部 `threading.Lock` 仅保护进程内 session store 内存读写，无需跨进程 | 保持现状；如未来改为进程间共享 store 再评估 |
| 6 | P2 | `tg_signer/utils.py` `atomic_write_json` | `os.replace` 会重置文件权限为 0644，可能暴露敏感信息 | 存在 | 写前记录原权限，replace 后恢复 |
| 7 | P2 | `backend/core/database.py` | `PRAGMA cache_size=-64000`（64MB 页）× QueuePool 多连接并发 → 页缓存峰值可达 ~960MB | 存在 | 评估 `cache_size` 与连接池上限，必要时降级 |
| 8 | P2 | `tests/test_routes_security.py` | 用真实 DB（`get_engine()`）未隔离，测试污染共享数据 | 存在 | 隔离到临时 DB fixture |
| 9 | P2 | `backend/` httpx/AsyncClient | 部分 HTTP 客户端未显式关闭 | 存在 | 统一 `async with` 生命周期 |

## 3. 同步流程（每次上游升级必做）

1. 对照 §1 逐模块重跑裁决，更新差异列。
2. 处理 §2 中标记「本次同步前需处理」的 P1（至少 #1 SPA 穿越、#5 tg_session 锁）。
3. 全量回归：`pytest tests -o addopts="-W=ignore::DeprecationWarning:pyrogram.utils"`
   + `ruff check backend/ tg_signer/` + 前端 `NODE_ENV=development npm run build`。
4. 更新 `doc/sync-amchii-*.md` 同步记录与 README 更新日志。

## 4. 面板 adapter 分层目标（0.10 减冲突）

面板相关逻辑应集中在 adapter/service 层，避免在上游核心方法中堆叠面板专用字段：

- API route（`backend/api/routes/`）
- scheduler adapter（`backend/scheduler/`）
- task execution service（`backend/services/sign_tasks.py` / `tasks.py`）
- log observer（`_active_logs` / WebSocket 推送）
- WebSocket adapter（`/api/sign-tasks/*/ws`）
- account lock service（`backend/utils/account_locks.py`）

推荐 observer 接收运行过程（`on_action` / `on_message` / `on_finished`），
使下一次同步上游 0.10 时减少 `core.py` 冲突（详见 `doc/review-upstream-0.9-optimization.md` §6.2）。

## 5. DB task 子进程依赖评估（修 8）

> 背景：`GPT-review-20260630-followup.md` §3.1/5.2/8 指出，legacy DB task
> 走 `APScheduler -> run_task_once -> async_run_task_cli -> tg-signer 子进程`，
> 与进程内 SignTask 是两套执行路径。

**已落地（本次）**：
- `run_task_once` 已按 `account.account_name` 获取并持有统一 `AccountLock`，
  覆盖「创建运行记录 -> 启动子进程 -> 等待完成」完整区间（`_run_task_once_locked`），
  与 SignTask 路径共享同一把锁 → 同账号并发已被进程内 + 跨进程文件锁阻断。
- 锁超时（`AccountLockTimeout`）写入失败记录（含 `worker_id`），不静默跳过。
- `TaskLog` 与 SignTask 运行状态均已携带 `worker_id`（pid@hostname），
  多 worker 可排查并发来源。

**现状评估**：
- DB task 子进程与 SignTask 进程内执行现在**互斥正确**，安全性已达标；
  子进程路径仍保留（继承的 `Task`/`TaskLog` 记录源 + CLI 兼容），但不再是并发风险源。
- 主要成本是子进程开销（每次 `tg-signer run` 起一个进程、重新载入 session），
  以及日志/回调通过 stdout 管道传输的间接性。

**收敛方向（后续迭代，未在本轮执行）**：
1. 让 `_job_run_task`（DB task）改为调用进程内 `SignTaskService.run_task_with_logs`，
   CLI 子进程仅保留为命令行兼容入口（`tg-signer run`）。
2. 统一 DB task 与 SignTask 的状态机为同一持久化状态机（run_id/state/started/finished/worker_id）。
3. 逐步弃用 `backend/models/task.py` 的布尔内存状态，统一到磁盘/DB 状态。

> 未执行原因：进程内化会改变日志捕获格式、`num_of_dialogs` 语义与 TaskLog 回写逻辑，
> 属行为级重构，需在后续迭代配合回归矩阵验证后单独推进；当前互斥已由 AccountLock 闭环。
