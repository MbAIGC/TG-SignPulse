# TG-SignPulse 第四轮 GPT Review

> Review 范围：最新提交相对于第三轮 Review 的后端任务状态统一、运行历史 SQLite 化、账号锁、CI/Docker 和回归测试。
>
> 本轮仅进行 Review 和验证，未修改源码及其他 Markdown 文件。

## 1. 总体结论

本轮版本继续有明显进步：

- Python 测试：**214 项全部通过**；
- `ruff check backend/ tg_signer/`：通过；
- 前端 `npm ci --include=dev && npm run build`：通过；
- DB task 已改为复用进程内 SignTask 执行链路；
- DB task 与 SignTask 已共享账号锁、`run_id`、`worker_id` 和运行状态；
- 运行历史已引入 SQLite 主存储；
- Dockerfile 和 CI 已显式安装前端 dev dependencies；
- 文件锁已支持非阻塞轮询、超时和取消。

但代码审查仍发现以下发布前需要处理的问题：

- **S1：DB task 同一任务并发检查与账号锁获取不是原子的，可能串行重复执行；**
- **S2：`start_task_run()` 的并发启动占位不是原子的，可能双启动并覆盖状态；**
- **S3：部分运行标志在异常安全边界外设置，数据库提交或初始化失败时可能永久假死；**
- **S4：SQLite 历史保留策略存在多账号互相挤占和并发写入风险；**
- **H1/H2/H3/H4/H5：旧 JSON 迁移、flow 截断、历史读取路径、原子写入和直调状态一致性仍不完整。**

因此当前版本评价为：

> **测试和构建质量良好，但任务并发、运行状态一致性和历史兼容仍存在实际逻辑风险，不建议在这些问题处理前作为正式稳定版发布。**

## 2. S1：DB task 同一任务并发可能重复执行

### 2.1 位置

- `backend/services/tasks.py:135-154`；
- `backend/services/tasks.py:178-289`。

当前流程先执行：

```python
if is_task_running(task.id):
    ...

account_lock = get_account_lock(account.account_name)
async with account_lock:
    return await _run_task_once_locked(...)
```

但进入锁之后没有再次检查 `is_task_running(task.id)`。

### 2.2 触发方式

两个并发调用同时进入：

```text
请求 A：检查 task running -> False
请求 B：检查 task running -> False
请求 A：获取账号锁，执行任务
请求 A：完成并释放账号锁
请求 B：获取账号锁，再次执行同一个 task
```

账号锁只保证串行，不保证同一个 task 只执行一次。因此手动运行和 scheduler 同 tick，或两个并发 API 请求，仍可能造成重复签到。

此外，命中运行状态时返回最近一条 `TaskLog`，它可能是上一次成功记录，甚至可能是 `None`，调用方容易把旧结果误认为本次执行结果。

### 2.3 建议

在锁内重新执行一次同任务状态检查，并使用原子占位：

```text
获取账号锁
  -> 检查 task_id 是否已有运行记录
  -> 创建 running TaskLog / run_id
  -> 执行任务
```

更推荐使用持久化运行记录或数据库唯一约束，确保跨协程、跨进程和重启场景都不会重复创建同一次运行。

API 层应返回明确的 `already_running` 状态和已有 `run_id`，而不是返回旧日志对象。

## 3. S2：`start_task_run()` 仍可能双启动

### 3.1 位置

`backend/services/sign_tasks.py:3268-3345` 附近的 `start_task_run()`。

当前检查 `_active_tasks` 后，真正设置运行标志要等后台 runner 进入 `run_task_with_logs()` 后才发生，约在 `sign_tasks.py:3417`。

### 3.2 风险

两个并发请求可能都看到未运行：

```text
A：检查 -> False，生成 run_id A
B：检查 -> False，生成 run_id B
A：注册 runner A
B：注册 runner B，并覆盖同一 task key 的后台任务引用
```

后果包括：

- 同一个任务创建两个运行；
- `_run_statuses` 被后一次运行覆盖；
- 第一个运行完成时因 run_id 不匹配而跳过状态更新；
- `/run/status` 只能看到最后一次运行；
- `_background_run_tasks` 只保留后一个引用，前一个任务可能失去生命周期管理。

### 3.3 建议

使用 task key 级别的启动锁，保护完整区间：

```text
检查 active
  -> 生成 run_id
  -> 写入 running 状态
  -> 注册后台 runner
```

或者使用 `_background_run_tasks` 的原子占位机制。并发调用时应保证：

- 只创建一个 runner；
- 只生成一个 run_id；
- 后续调用返回同一 run_id 和 `already_running`；
- 状态完成、取消和清理都只影响对应 run_id。

应增加 `asyncio.gather()` 并发启动回归测试。

## 4. S3：运行标志异常泄漏，可能导致永久假死

### 4.1 位置

- `backend/services/tasks.py:180-205`；
- `backend/services/sign_tasks.py:3417-3453`。

当前部分 `_active_tasks[...] = True` 在完整 `try/finally` 保护之外。若以下操作抛异常：

- 数据库 `commit()`；
- 任务状态持久化；
- 日志初始化；
- 配置加载；
- 运行状态初始化；

则 finally 可能尚未建立，运行标志不会恢复为 False。

### 4.2 影响

之后调用会持续命中：

```python
is_task_running(...) is True
```

任务表现为永久 running，新的运行请求被拒绝或静默返回旧状态。

### 4.3 建议

将“设置运行标志”放进受保护的执行函数，并确保所有早期异常都经过统一清理：

```text
try:
    设置 running
    创建记录
    执行
finally:
    清除 running
    取消日志桥接任务
    释放资源
    持久化最终状态
```

同时增加数据库 commit 失败、运行状态写入失败和 runner 创建失败测试。

## 5. S4：SQLite 历史截断策略存在一致性风险

### 5.1 位置

`backend/services/run_history.py:90-128`。

当前每次写入大致是：

1. INSERT；
2. 单独提交；
3. 查询某任务的全部 id；
4. 删除超出上限的记录；
5. 再次提交。

### 5.2 问题

#### 多账号互相挤占

截断按 `task_name`，没有按 `account_name` 区分。同名任务的多个账号共用 `max_entries` 配额，账号 A 的大量运行可能挤掉账号 B 的历史。

#### 并发写入不是一个事务

INSERT 和 SELECT/DELETE 使用两次提交。多个 writer 并发时，截断查询和删除可能基于不同快照，误删并发新插入的记录。

### 5.3 建议

- 明确保留上限是“每任务”还是“每任务/账号”；
- 如果按账号展示历史，建议按 `(task_name, account_name)` 截断；
- 将 INSERT 和截断放在同一个事务内；
- 使用 SQLite 事务锁和索引优化；
- 以 `id` 或唯一 `run_id` 为稳定删除依据；
- 增加两个账号并发写入测试。

## 6. H1：旧 JSON 历史没有真正迁移到 SQLite

### 6.1 位置

- `backend/services/run_history.py:130-147`；
- `backend/services/sign_tasks.py:872-920`；
- `backend/services/sign_tasks.py:1522-1587`。

读取逻辑是 SQLite 有数据就直接返回 SQLite，只有 SQLite 完全没有记录时才回退 JSON。写入新记录时虽然同时写 SQLite 和 JSON，但没有把升级前已有 JSON 记录导入 SQLite。

### 6.2 影响

升级前 JSON 中有多条历史时，升级后首次运行会产生一条 SQLite 记录。此后读取只看到 SQLite 记录，旧 JSON 历史从 Web/API 视角消失。

### 6.3 建议

增加幂等迁移：

- 首次初始化或首次查询时扫描旧 JSON；
- 按 `run_id` 去重；
- 老记录没有 `run_id` 时使用稳定组合键或哈希；
- 写入迁移版本标记；
- 迁移完成后 JSON 作为只读备份；
- 迁移失败时保留 JSON fallback 并输出 warning。

## 7. H2：flow 截断配置是死配置

### 7.1 位置

- `backend/services/sign_tasks.py:152-160`；
- `backend/services/sign_tasks.py:859-870`。

`SIGN_TASK_HISTORY_MAX_FLOW_LINES` 和 `SIGN_TASK_HISTORY_MAX_LINE_CHARS` 被读取，但 `_normalize_flow_logs()` 仍保留完整列表和完整行，`flow_truncated` 基本恒为 False。

### 7.2 影响

超长日志会同时进入：

- 内存；
- JSON；
- SQLite；
- API 响应；
- WebUI 历史详情。

配置项对实际资源没有保护作用。

### 7.3 建议

在规范化阶段真正执行：

- 每行最大字符数截断；
- 总 flow 行数截断；
- 保留原始数量；
- 正确设置 `flow_truncated`；
- API 返回截断标志。

## 8. H3：部分历史读取仍绕过 SQLite 主存储

### 8.1 位置

- `backend/services/sign_tasks.py:1024-1075`；
- `backend/services/sign_tasks.py:1496-1520`。

部分账号历史和 `last_run` 逻辑仍直接读取 JSON，而其他历史 API 已优先读 SQLite。

### 8.2 影响

- SQLite 写入成功、JSON 写入失败时，部分接口漏记录；
- JSON 文件清理后 `last_run` 可能陈旧；
- 不同 API 返回的最新记录不一致；
- 面板展示与 CLI 结果可能不一致。

### 8.3 建议

所有历史读取统一走 `RunHistoryStore`：

- 账号历史；
- task history；
- detail；
- last run；
- clear/delete；
- filtered history。

JSON 只作为一次性迁移和兼容备份。

## 9. H4：JSON/config 写入和运行状态恢复仍有一致性风险

### 9.1 位置

- `backend/services/sign_tasks.py:1586`；
- `backend/services/sign_tasks.py:1605-1609`；
- `backend/services/sign_tasks.py:3198-3204`。

部分 JSON 和 config 文件仍使用直接 `open(..., "w")` 写入，进程在写入过程中崩溃可能留下半截文件。

另外，重启时将 running 状态修正为 cancelled 的逻辑需要确保最终状态持久化到磁盘，并清理过期状态文件。否则磁盘状态和内存状态可能不一致。

### 9.2 建议

- 统一使用项目已有的 `atomic_write_json`；
- 使用临时文件 + fsync + replace；
- 对 config 和 history 更新采用同一套原子写策略；
- 状态恢复时立即把 cancelled 状态写回磁盘；
- 清理任务按 `run_id` 和完成时间清理，避免只按 task key 覆盖。

## 10. H5：直调 `/run` 状态与历史 run_id 仍可能不一致

### 10.1 位置

- `backend/api/routes/sign_tasks_v2.py:386-414`；
- `backend/services/sign_tasks.py:3384-3422`。

`/run` 直调调用 `run_task_with_logs()`，但运行状态管理主要在 `lock_already_held=True` 的 DB task 路径启用。直调路径可能不创建持久化 run status，`_save_run_info()` 再自行生成新的 `run_id`。

### 10.2 影响

- `/run/start` 与 `/run` 的状态语义不同；
- `/run/status` 可能返回 idle 或旧状态；
- 历史中的 run_id 无法对应状态记录；
- 前端无法可靠地追踪一次直调运行。

### 10.3 建议

统一所有运行入口：

```text
创建 run_id
  -> 持久化 running
  -> 执行任务
  -> 持久化 success/failed/cancelled
  -> 写历史并复用同一 run_id
```

不要让 `_save_run_info()` 在已有运行上下文缺失时静默生成无法追踪的 run_id；应由统一入口生成并显式传递。

## 11. 其他中低优先级观察

### M1：账号检查仍可能在锁外执行

`_check_account_before_task()` 位于完整账号执行锁保护范围之外。同一账号的并发任务仍可能同时发起 Telegram 检查请求。建议将检查放进账号锁，或者提供只读连接检查的专用并发策略。

### M2：未真正执行的任务可能更新账号冷却时间

如果任务因账号无效、配置错误或提前结束，仍更新 `_account_last_run_end`，下一次合法执行可能被无谓推迟。建议只在实际完成 Telegram 动作后更新冷却时间。

### M3：异常和取消后的 signer/client 清理需要统一

任务异常或超时后，应确认 signer、客户端引用、handler、日志桥接任务和锁都释放。建议增加取消路径和资源泄漏测试。

### M4：DB task 日志文件名精度不足

`backend/services/tasks.py:128-132` 使用秒级时间戳。同一个 task 在同一秒内重复创建日志文件时可能覆盖前一次文件。建议加入 `run_id` 或微秒/随机后缀。

### M5：跨进程锁配置依赖部署方式

Docker Compose 默认打开 `ACCOUNT_LOCK_FILE=1`，但直接运行应用或自定义部署仍可能默认关闭。多 worker 部署必须确认显式启用跨进程锁，并确认锁目录可写。

### M6：按时间删除历史可能误删多条

`delete_history_log()` 主要按 `time` 定位记录，时间字段不是数据库唯一键。建议 API 使用数据库 id 或 `run_id` 删除，时间只作为兼容 fallback。

### M7：运行历史截断复杂度偏高

每次写入都查询某任务的全部 id 再删除，历史较多时为 O(n)。建议使用索引、按 id 定向删除或后台清理。

### M8：运行历史 store 缓存和线程连接管理

`get_run_history_store()` 的全局缓存没有锁，`RunHistoryStore.close()` 只关闭当前线程连接。多线程/多 worker 场景建议明确连接生命周期和缓存同步策略。

### M9：时间标准不统一

历史记录使用本地 naive `datetime.now()`，运行状态使用 UTC ISO 时间。建议所有持久化时间统一为 UTC，并在展示层转换时区。

### M10：过期状态查询语义不明确

运行状态清理后，旧 `run_id` 查询返回 idle，容易与“从未运行”混淆。建议返回 `stale` 或 `not_found`，并保留最小审计信息。

## 12. 已确认的优点

- 214 项 Python 测试全部通过；
- Ruff 后端和核心库检查通过；
- 前端 TypeScript 检查、Vite 构建和 PWA 构建通过；
- DB task 已与进程内 SignTask 统一执行链路；
- 账号级锁已支持进程内互斥、跨进程文件锁、超时和取消；
- `run_id` / `worker_id` 已进入运行记录和状态模型；
- 日志路径越界、符号链接、大文件读取已有防护；
- JWT 弱密钥、session string 文件权限和 WebSocket 首帧认证已有加固；
- 上游 0.9 RouteKey、多 chat 缓存和配置迁移已有回归覆盖；
- CI 已加入后端 lint/test 和前端构建门禁。

## 13. 建议整改顺序

### 发布前必须处理

1. 修复 DB task 同任务检查与加锁的 TOCTOU；
2. 修复 `start_task_run()` 双启动和状态覆盖；
3. 将所有运行标志纳入统一 try/finally；
4. 修复 SQLite 历史截断的账号隔离和事务一致性；
5. 完成旧 JSON 到 SQLite 的幂等迁移；
6. 让 `/run`、`/run/start`、DB task 使用统一 run status 和 run_id；
7. 实际启用 flow 行数和单行字符数限制。

### 后续迭代

8. 所有历史读取统一走 SQLite store；
9. 全面使用原子 JSON/config 写入；
10. 统一 UTC 时间；
11. 用 id/run_id 替代时间删除历史；
12. 增加多 worker、并发启动、数据库失败、取消和重启恢复测试；
13. 逐步移除 Web 调度对子进程 CLI 的依赖；
14. 完成 Docker 干净环境和真实 Telegram session 验证。

## 14. 最终发布判断

当前版本可继续作为 Release Candidate 验证，但不建议直接标记为正式稳定版。

主要阻塞原因不是构建或基础安全，而是运行时一致性：

> **同一任务的并发启动、运行状态占位、历史迁移和 SQLite 截断仍可能造成重复执行、状态丢失或旧历史不可见。**

完成第 13 节中的发布前项目，并补充对应回归测试后，才建议进入正式发布阶段。
