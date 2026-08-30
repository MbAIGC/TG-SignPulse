# TG-SignPulse 上游 tg-signer 0.9 兼容性 Review

> Review 范围：当前 `TG-SignPulse` 分支对上游 `amchii/tg-signer 0.9.0` 的同步结果，以及面板侧对客户端生命周期、任务调度、配置迁移和记录存储的适配。
>
> Review 结论：**库层主体兼容方向正确，但面板调度层存在双轨执行和生命周期偏差。建议在继续同步上游前，优先收敛客户端、任务和存储边界。**

## 1. 总体评价

本次 0.9 同步保留了上游的主要能力：

- RouteKey 消息路由：`(chat_id, message_thread_id)`；
- 自动化规则引擎；
- SQLite 签到记录和旧 JSON 兼容迁移；
- Kurigram 2.2.x 会话适配；
- 文件夹、topic、用户名 chat 等 CLI 能力；
- 配置模型和 V2/V3 迁移支持。

当前 Python 测试已通过 166 项，说明库层的核心回归基础较好。但 Web 面板引入了独立的 scheduler、子进程任务执行、进程内客户端、历史 JSON 和 WebSocket 状态，这些外围流程没有完全收敛到 0.9 的生命周期和存储语义。

因此当前状态更准确地描述为：

```text
上游库功能：基本跟进 0.9
面板 API：功能完整，但存在独立实现
任务执行：进程内 / 子进程双轨
记录存储：SQLite / history JSON 双轨
客户端生命周期：部分路径与上游及本地增强语义不一致
```

## 2. P0/P1：建议优先处理的问题

### 2.1 `normal_run` 部分路径绕过统一客户端生命周期

**涉及位置：** `tg_signer/core.py` 的 `normal_run` 及相关 `app.start()` / `app.stop()` 路径。

上游 0.9 使用 `async with self.app` 管理客户端。当前面板适配后的部分逻辑直接调用底层 `start()` 和 `stop()`，可能绕过本地增强过的：

- DB 锁重试；
- unauthorized 显式检测；
- 连接失败回滚；
- 客户端引用计数；
- 统一清理逻辑。

这会造成一个重要的不一致：文档声明启动期无效会话会显式报错，但文件模式的 `normal_run` 可能继续沿用底层启动语义，错误延迟到后续 API 调用才暴露。

**建议：**

- 统一提供内部客户端上下文，例如 `_client_context()`；
- `normal_run`、`run_once`、自动化和监听器都通过该上下文进入客户端；
- 不要在业务路径中直接调用 `app.start()` / `app.stop()`；
- 保留面板需要的“启动期 ConnectionError 显式抛出”语义。

### 2.2 `sign_once` 不应清空所有 route key 的消息

当前面板逻辑可能在一个 chat 处理完成后清空全部消息缓存，而上游 0.9 只清理当前 chat 的 route key。

多 chat 场景下可能发生：

1. A chat 正在处理；
2. B chat 的目标消息已进入缓存；
3. A chat 完成后清空全部 route key；
4. B chat 丢失缓存，只能回退扫描历史，甚至直接漏处理。

**建议：**只清理当前路由：

```python
self.context.chat_messages[route_key].clear()
```

不得使用遍历所有 route key 的全量清理，除非任务生命周期明确结束并且确认没有其他 chat 正在消费消息。

### 2.3 修复 V1 → V3 配置迁移链

当前 V2 → V3 迁移存在，但直接执行 `SignConfigV3.load(V1_dict)` 可能返回 `None`。旧版 V1 配置通常是顶层 `chat_id` / `sign_text` 结构，面板或 CLI 在加载失败后可能出现 `None` 解包或不友好的 `TypeError`。

**建议：**

- `BaseJSONConfig.load()` 递归遍历完整的 `V3 -> V2 -> V1` 链；
- 或在 `load_config()` 中显式尝试所有历史版本；
- 迁移成功后立即写回当前版本；
- 写回前创建 `.bak` 备份；
- 迁移失败返回包含版本和字段提示的可读错误，而不是裸 `TypeError`。

这是兼容性承诺中的关键路径，建议发布前完成。

### 2.4 建立统一的账号级执行锁

当前存在两条任务执行轨道：

#### 数据库任务

```text
APScheduler
  -> backend scheduler
  -> tg-signer 子进程
  -> 文件模式 session
```

#### 签到任务

```text
APScheduler
  -> sign_task_service
  -> 进程内客户端
  -> in-memory session
```

两条轨道之间没有统一账号级互斥。同一账号在相近时间执行任务时，可能同时访问同一个 `.session` 文件或同时建立不同客户端，导致：

- SQLite session 锁冲突；
- Telegram 连接互相干扰；
- 重复执行；
- 一条轨道无法感知另一条轨道的运行状态；
- 多 worker 部署时状态进一步分裂。

**建议：**引入统一的 `AccountExecutionLock`，所有以下入口必须经过同一套锁：

- Web 手动运行；
- APScheduler；
- CLI 任务；
- 自动化规则；
- 关键词监听；
- DB task；
- sign task。

锁至少应包含：

- `account_name`；
- 当前 `run_id`；
- 任务类型；
- 获取时间和超时时间；
- 异常释放机制。

短期可以保留子进程，但必须通过跨进程锁实现账号级互斥。长期建议 Web 面板统一调用进程内 service，CLI 子进程只作为 CLI 兼容入口。

## 3. P1：客户端 key 和监听流程

### 3.1 统一客户端 key 生成逻辑

in-memory 模式下客户端可能使用带 `::memory` 后缀的 key，而关键词监听部分仍可能只按基础账号名查找。这会导致监听器误判客户端不存在，随后错误复用 `no_updates=True` 的客户端。

表现为：

- 监听器看似启动成功；
- 客户端实际不接收 updates；
- 关键词动作没有触发；
- 没有明显错误日志。

**建议：**提供统一的 key 计算函数，例如：

```python
def get_client_key(account_name, session_mode, no_updates):
    ...
```

所有业务模块统一调用，不允许自行拼接：

- `get_client()`；
- `close_client_by_name()`；
- keyword monitor；
- automation；
- scheduler；
- backend task service。

### 3.2 修复 `close_client_by_name()` 的双 key 清理

清理基础 key 和 `account::memory` key 时，如果先处理的 key 仍有引用，当前逻辑可能直接 `return`，导致另一个 key 永远不会被处理。

**建议：**将早退改为继续处理，或者把每个 key 的清理放入独立 helper，确保两个 key 都得到评估。清理过程还应记录：

- 是否仍有引用；
- 是否断开连接；
- 是否保留实例等待引用归零；
- 是否发生超时。

### 3.3 重连路径应与首次进入路径保持一致

共享客户端在外部断开后再次进入时，部分路径只执行 `connect()`，没有完整执行：

- 连接结果检查；
- `get_me()` 握手；
- unauthorized 检查；
- DB 锁重试；
- 失败回滚和实例清理。

**建议：**首次连接和重连共用一个 `_ensure_app_ready()` 或等价的握手方法，不要维护两套不同的成功判定逻辑。

## 4. P1/P2：任务调度流程优化

### 4.1 range 模式不应在 scheduler job 内长时间 sleep

当前随机时间段模式在 APScheduler job 内直接 `asyncio.sleep(delay_seconds)`。当时间窗口较大时，一个 job 实例会长时间处于运行状态。

可能影响：

- 调度积压；
- 任务取消延迟；
- 服务重启后重新随机；
- 同一账号的多个任务同时等待；
- 调度状态不易观察。

**建议：**

- 计算随机执行时间后创建一次性 `date` job；或
- 把当天随机计划持久化；
- 服务重启时恢复已有计划，而不是重新随机；
- 执行前重新检查任务是否启用、账号是否被占用；
- 每次运行生成唯一 `run_id`。

### 4.2 任务状态从布尔值升级为状态机

目前部分任务状态依赖进程内字典，例如 `_active_tasks` / `_active_logs`。建议统一使用明确状态：

```text
pending -> acquiring_lock -> running -> success
                                      -> failed
                                      -> cancelled
                                      -> timeout
```

状态应持久化关键字段：

- `run_id`；
- `account_name`；
- `task_name`；
- `started_at`；
- `finished_at`；
- `worker_id`；
- 错误类型；
- 是否可重试。

这样服务重启、多 worker 和 WebSocket 重连时都能恢复状态，而不是依赖内存快照。

### 4.3 避免同一任务并发运行时静默返回旧日志

`run_task_once()` 检测到任务运行时，可能直接返回最近一条日志。这会让调用方难以区分：

- 本次请求是否真的启动；
- 返回的是哪一次运行；
- 当前任务是否仍在执行。

**建议：**返回明确的运行结果，例如：

```json
{
  "started": false,
  "state": "already_running",
  "run_id": "..."
}
```

API 层可使用 `409 Conflict` 或稳定错误码，前端据此连接到已有运行，而不是误认为启动成功。

## 5. P1/P2：记录存储统一

### 5.1 面板历史与 SQLite 记录目前存在双轨

上游 0.9 已将签到记录主存储切换到 `data.sqlite3`，旧 `sign_record.json` 仅用于兼容读取和迁移。但当前面板历史 API 仍主要读取 workdir 下的 history JSON。

这会造成：

- CLI 和 Web 面板看到的记录不一致；
- SQLite 迁移完成后面板仍看不到历史；
- 清理、筛选、导出行为不一致；
- 用户无法判断哪个数据源是事实来源。

**建议目标：**

```text
SQLite = 唯一主记录源
旧 JSON = 只读兼容 / 一次性迁移
```

建议步骤：

1. 后端历史 API 接入 `SignRecordStore`；
2. 兼容读取旧 history JSON；
3. 迁移完成后记录迁移标记；
4. 新写入不再创建新的主 JSON；
5. CLI、API、WebSocket 使用同一套记录查询服务；
6. 增加 CLI 与 API 查询结果一致性测试。

### 5.2 SQLite 连接统一设置 WAL 和 busy timeout

`SignRecordStore` 应与 session 数据库保持一致的并发策略，建议在连接初始化时设置：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=10000;
```

同时对写入增加有限次数、带退避的重试。不要依赖上层偶然捕获 `database is locked`。

### 5.3 日志文件删除和读取都应限制在日志目录内

旧日志清理应验证路径位于 `logs_dir` 内，避免数据库中的异常路径导致删除目录外文件。

完整日志读取不应无上限执行 `read()`，建议：

- 限制最大读取大小；
- 默认读取尾部；
- 支持分页；
- 拒绝符号链接或非普通文件；
- 返回截断标志。

## 6. 代码分层建议

### 6.1 保持上游兼容层稳定

上游兼容层建议只负责：

- 配置模型和迁移；
- Telegram API 兼容；
- RouteKey；
- `wait_for`；
- `normal_run`；
- 自动化引擎；
- SQLite 签到记录。

### 6.2 将面板功能移动到 adapter/service 层

面板相关逻辑建议集中在：

- API route；
- scheduler adapter；
- task execution service；
- log observer；
- WebSocket adapter；
- account lock service。

不要继续在上游核心方法中堆叠面板专用字段和状态，例如 WebSocket marker、后端 task id 或 UI 专用 callback 状态。

推荐使用 observer 方式接收运行过程：

```python
class RunObserver:
    async def on_action(self, event): ...
    async def on_message(self, event): ...
    async def on_finished(self, result): ...
```

这样下一次同步上游 0.10 时，可以减少 `core.py` 的冲突。

### 6.3 建立同步裁决表和兼容性测试矩阵

每次同步上游前，维护以下信息：

| 模块 | 上游语义 | 面板差异 | 裁决 | 回归测试 |
|---|---|---|---|---|
| client lifecycle | `async with` | 面板需要显式异常 | 保留本地增强 | 连接、重连、引用计数 |
| route key | `(chat_id, thread_id)` | 面板日志扩展 | 以 0.9 为准 | 多 chat、topic |
| sign records | SQLite | 旧 history JSON | SQLite 为主 | 迁移、查询一致性 |
| scheduler | 库内运行 | 面板有子进程任务 | 逐步统一 | 账号互斥 |
| config migration | V1/V2/V3 | 面板字段扩展 | 递归兼容 | V1→V3 |

## 7. 建议新增的回归测试

### 7.1 客户端生命周期

- 文件模式连接失败会抛出明确异常；
- `async with` 多引用计数正确；
- `no_updates=True/False` 不会错误复用；
- 基础 key 和 `::memory` key 都能关闭；
- 并发启动同一账号不会重复连接；
- 重连路径执行完整握手和授权检查。

### 7.2 多聊天和 topic

- A chat 完成不会清空 B chat 的消息；
- topic route 与非 topic route 同时存在时能正确消费；
- `@username` 解析缓存能跨 action 保留；
- 一个 chat 解析失败不会影响其他 chat；
- 已消费消息占位逻辑不会重复触发。

### 7.3 配置迁移

- V1 → V2；
- V1 → V3；
- V2 → V3；
- 迁移后配置写回和备份；
- 非法配置返回稳定错误码；
- 重复迁移幂等。

### 7.4 调度和并发

- DB task 与 sign task 同账号互斥；
- 同一 task 并发运行只创建一个 run；
- 多 worker 或跨进程锁行为正确；
- range 模式取消和重启不会重复执行；
- 任务异常后锁和客户端都能释放。

### 7.5 存储和 API

- SQLite 与旧 JSON 同时存在时 SQLite 优先；
- Web API 与 CLI 返回相同记录；
- SQLite locked 时可以重试；
- 大日志读取有上限；
- 日志路径不能越出日志目录。

## 8. 推荐实施顺序

### 第一阶段：先修正确性

1. 修复 `sign_once` 全量清缓存；
2. 修复 V1 → V3 迁移；
3. 统一 `normal_run` 生命周期；
4. 修复客户端 key 和双 key 清理；
5. 修复监听器错误复用 `no_updates` 客户端的问题。

### 第二阶段：收敛调度

6. 建立统一账号级执行锁；
7. 为任务引入 `run_id` 和状态机；
8. 避免 range job 长时间 sleep；
9. 减少 Web scheduler 对 CLI 子进程的依赖；
10. 增加异常恢复和取消流程。

### 第三阶段：统一数据

11. 后端历史 API 接入 `SignRecordStore`；
12. 统一 SQLite WAL、busy timeout 和重试；
13. 统一日志文件读取、清理和导出；
14. 增加 API/CLI 数据一致性测试。

### 第四阶段：改善长期维护

15. 将面板 adapter 与上游核心分层；
16. 建立上游同步裁决表；
17. 固定上游版本和依赖验证矩阵；
18. 每次同步自动执行生命周期、配置、调度和存储回归测试。

## 9. 当前验证状态

已完成的验证：

- Python 测试：166 项通过；
- `tg_signer/` 核心代码已按 0.9 同步并有对应测试；
- RouteKey、topic、用户名 chat、SQLite 迁移、自动化规则均有覆盖；
- 已确认面板调度和库层存在进程内 / 子进程双轨；
- 已确认面板历史和 SQLite 记录存在双轨；
- 已确认 V1 直接加载 V3 存在迁移链风险。

仍需补充验证：

- 真实 Telegram 会话下的关键词监听复用场景；
- 文件模式 `normal_run` 的锁冲突和 unauthorized 行为；
- 多 worker 部署下的账号互斥；
- Docker 干净环境构建；
- 大日志和长时间 range 任务；
- SQLite 高并发写入。

## 10. 最终结论

上游 0.9 的库功能建议继续保留，尤其是 RouteKey、自动化引擎、SQLite 记录和 Kurigram 兼容层。后续重点不应是继续整体覆盖上游文件，而应是：

> **统一客户端生命周期，统一账号级执行锁，统一 SQLite 记录源，并将面板逻辑收敛到 adapter/service 层。**

当前最大风险不是上游能力缺失，而是同步完成后，面板外围形成了另一套独立的客户端、调度和存储模型。只要优先解决上述边界问题，后续继续兼容上游版本会更稳定，升级冲突也会明显减少。
