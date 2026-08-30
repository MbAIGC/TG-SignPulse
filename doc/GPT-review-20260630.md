# TG-SignPulse 新版本升级 Review

> Review 范围：当前 TG-SignPulse 仓库的后端、前端、Docker 配置、测试和静态检查结果。
>
> Review 类型：升级后的整体代码质量、安全性、构建和运行风险审查。

## 1. 总体结论

本次升级功能覆盖较完整，后端测试全部通过，但当前版本**不建议直接作为稳定版发布**。

主要原因：

- 存在 Docker Compose 默认弱 JWT 密钥；
- WebSocket 使用 URL 查询参数传递 JWT；
- Telegram 会话字符串落盘保护仍需加强；
- 任务接口尚未体现用户级资源隔离；
- 任务执行存在并发竞态和多轨执行问题；
- Ruff 静态检查失败；
- 当前本地环境无法完成前端生产构建验证。

## 2. P0：高优先级问题

### 2.1 Docker Compose 默认使用公开弱密钥

**位置：** `docker-compose.yml:12`

```yaml
- APP_SECRET_KEY=${APP_SECRET_KEY:-your_secret_key}
```

如果部署者没有显式设置 `APP_SECRET_KEY`，JWT 会使用固定的公开字符串。任何知道该默认值的人都可能伪造 JWT，访问管理接口。

代码中的自动生成逻辑无法抵消这个问题，因为 Compose 注入的环境变量会优先覆盖自动生成逻辑。

**建议：**

- 删除固定默认值，未配置时拒绝启动；或
- 使用随机生成并持久化的密钥；
- 启动时检查密钥长度；
- 启动时拒绝 `your_secret_key` 等已知弱值；
- 在部署文档和 `.env.example` 中明确要求设置密钥。

### 2.2 WebSocket 通过 URL 查询参数传递 JWT

**位置：** `backend/api/routes/tasks.py:134-146`、`frontend/src/components/tasks/TaskLogsModal.vue:63-65`

当前连接形式类似：

```text
/api/sign-tasks/ws/...?...&token=...
```

JWT 可能出现在：

- 反向代理访问日志；
- 浏览器历史；
- 监控系统；
- 错误追踪系统；
- 网络调试记录。

**建议：**

- 优先通过 WebSocket 建立连接后的首条消息传递 token；
- 或实现一次性、短时有效的 WebSocket ticket；
- 限制 WebSocket token 的有效期；
- 配置反向代理不记录 query string；
- 在 token 泄露假设下缩短 JWT 生命周期。

### 2.3 Telegram session string 的磁盘保护不足

**位置：** `backend/utils/tg_session.py:311-368`

启动时会把 `.session` 转换为 `.session_string` 并缓存到磁盘。session string 等同于 Telegram 登录凭据，泄露后可能导致 Telegram 账号被接管。

当前需要重点确认：

- `.session_string` 是否设置为仅所有者可读；
- Docker volume 中的文件是否会被普通用户读取；
- 会话字符串是否会进入备份、日志或错误输出；
- 启动时批量导出是否会扩大敏感凭据暴露面。

**建议：**

- 创建和写入文件后设置 `0600` 权限；
- 对 session 目录设置最小权限；
- 避免在日志和 API 响应中输出完整 session string；
- 在部署文档中明确 `/data` 的权限要求；
- 评估是否必须在启动时批量导出所有会话。

## 3. P1：安全与运行时问题

### 3.1 任务接口认证后没有用户级资源隔离

**位置：** `backend/api/routes/tasks.py:30-131`

所有已登录用户都可以查看、修改、删除、运行任意任务，也可以使用任意账号和查看任意任务日志。

当前系统看起来按单管理员模式设计，但代码没有体现资源所有权。如果未来支持多用户，这会直接成为越权问题。

**建议：**

- 如果明确只支持单用户，应在配置、部署文档和产品说明中声明；
- 如果要支持多用户，为 `Account` 和 `Task` 增加 `owner_id`；
- 所有查询、修改、删除、运行和日志接口按当前用户过滤；
- 对管理员操作和普通用户操作区分权限。

### 3.2 登录和任务日志存在敏感信息暴露风险

**位置：** `backend/api/routes/logs.py:71-213`

登录审计日志返回：

- IP 地址；
- User-Agent；
- 用户名；
- 登录详情。

任务日志可能返回 Telegram 任务执行流程和完整输出。虽然接口需要登录，但缺少更细粒度的权限、脱敏和访问控制。

**建议：**

- 对 Telegram 输出中的手机号、用户名、消息内容和凭据相关字段脱敏；
- 详细日志单独设置权限；
- 对审计日志增加管理员权限判断；
- 限制单条日志和完整日志文件的返回大小；
- 记录日志访问审计。

### 3.3 完整日志读取没有大小限制

**位置：** `backend/api/routes/tasks.py:193-228`

当前使用无上限读取：

```python
with open(target_path, "r", encoding="utf-8") as f:
    content = f.read()
```

如果日志文件很大，接口会一次性将其读入内存，可能造成内存峰值甚至 OOM。

**建议：**

- 限制最大读取大小；
- 默认只返回日志尾部，例如最后 1–5 MB；
- 增加分页或 offset/limit；
- 返回 `truncated` 标记；
- 对日志下载和在线预览采用不同接口。

### 3.4 任务执行检查与设置存在并发竞态

**位置：** `backend/services/tasks.py:116-130`

当前逻辑先检查任务状态，再设置运行状态：

```python
if is_task_running(task.id):
    ...
_active_tasks[task.id] = True
```

检查和设置不是原子操作。多个请求或 scheduler 同时触发同一任务时，可能都通过检查并同时启动任务。

此外，`_active_tasks` 和 `_active_logs` 是进程内全局字典：

- 多 worker 部署时每个进程的状态不一致；
- 服务重启后状态丢失；
- 不同进程可能重复执行同一任务。

**建议：**

- 使用 `asyncio.Lock` 或线程安全锁保护“检查 + 设置”；
- 使用数据库运行记录或外部锁实现跨进程互斥；
- 明确限制多 worker 部署，或设计分布式锁；
- 为每次运行生成唯一 `run_id`；
- 对任务状态使用持久化状态机，而不只是布尔值。

### 3.5 旧日志清理的路径未限制在日志目录内

**位置：** `backend/services/tasks.py:37-61`

当前根据数据库中的 `log_path` 直接删除文件：

```python
p = Path(log_path)
if p.exists():
    p.unlink()
```

虽然正常路径由程序生成，但如果数据库内容被篡改或历史数据异常，维护任务可能删除日志目录之外的文件。

**建议：**

- 将路径解析为绝对路径；
- 验证其位于 `settings.resolve_logs_dir()` 内；
- 拒绝符号链接和非普通文件；
- 删除失败时记录完整服务端日志，但不要中断整个清理任务。

## 4. P1/P2：构建与代码质量问题

### 4.1 Ruff 静态检查失败

运行：

```bash
source .venv/bin/activate
python -m ruff check .
```

结果：16 个错误，均为 `E402 Module level import not at top of file`，涉及：

- `backend/api/routes/sign_tasks_v2.py`；
- `backend/api/routes/user.py`；
- `backend/core/rate_limit.py`；
- `backend/main.py`；
- `backend/utils/tg_session.py`。

部分文件存在运行时兼容代码或 SQLite monkey patch，因此导入顺序可能是有意设计，但当前没有局部豁免或清晰说明。

**建议：**

- 普通 import 移到文件顶部；
- 确实需要延迟导入的地方使用精确的 `# noqa: E402`；
- 避免全局忽略 E402；
- 将 monkey patch 封装到明确的初始化函数中；
- CI 将 `ruff check .` 作为发布前检查。

### 4.2 前端当前无法在本地完成生产构建

第一次执行：

```bash
npm run build
```

失败：

```text
sh: 1: vue-tsc: not found
```

随后执行 `npm ci` 时失败：

```text
The `npm ci` command can only install with an existing package-lock.json
```

仓库中虽然存在 `frontend/package-lock.json` 路径，但当前 npm 没有识别到可用的 lockfile。需要确认文件是否为空、格式是否兼容或是否与 `package.json` 不一致。

**建议：**

```bash
ls -l frontend/package-lock.json
head -20 frontend/package-lock.json
node --version
npm --version
```

然后在干净环境中重新生成并提交有效 lockfile。由于 Dockerfile 使用 `npm ci`，该问题可能直接导致 Docker 构建失败。

### 4.3 前端依赖升级需要在干净环境中验证

`frontend/package.json` 当前使用较新的工具链：

- Vue 3.5；
- Vite 8；
- TypeScript 6；
- vue-tsc 3；
- Node 20.19+ 或 22.12+。

Dockerfile 使用 Node 22 Alpine，理论上版本范围匹配，但本地 lockfile 和依赖必须一致。

**建议：**

- 在 Node 22 的干净 Docker 环境执行 `npm ci && npm run build`；
- 将构建结果作为 CI 必检项；
- 固定可复现的依赖树；
- 不要只依赖当前工作目录中的 `node_modules`。

### 4.4 配置异常可能把内部实现细节返回给客户端

例如：

- `backend/api/routes/config.py:78-82`；
- `backend/api/routes/config.py:109-113`；
- `backend/api/routes/config.py:228-231`；
- `backend/api/routes/tasks.py:226-229`。

当前错误响应中直接拼接 `str(e)`，可能暴露：

- 本地路径；
- 数据库结构；
- 第三方库错误；
- 文件系统信息。

**建议：**

- 服务端记录完整异常和 traceback；
- 客户端返回稳定错误码和通用消息；
- 仅在开发环境返回详细错误；
- 对外错误响应统一格式。

## 5. 已完成验证

### 5.1 Python 测试

已执行：

```bash
source .venv/bin/activate
python -m pytest -vv tests/
```

结果：

```text
166 passed in 15.53s
```

现有测试覆盖了：

- 密码哈希和 JWT；
- TOTP 生命周期；
- 路径穿越；
- 速率限制；
- 账号并发存储；
- 签到配置迁移；
- SQLite 签到记录；
- Telegram topic/thread 兼容；
- 自动化规则。

### 5.2 Ruff

执行失败：

```text
16 errors: E402
```

### 5.3 前端

未能完成生产构建验证，原因：

1. 当前环境缺少 `vue-tsc`；
2. `npm ci` 无法识别可用的 lockfile。

因此目前不能确认 TypeScript 类型检查和 Vite 生产构建是否通过。

## 6. 测试覆盖缺口

现有测试主要集中在单进程和 mock 场景，建议补充：

- 多 worker 部署场景；
- WebSocket 断线和并发运行；
- 同一任务并发触发；
- 同一账号不同任务同时执行；
- 大日志文件读取；
- 日志路径越界和符号链接；
- session string 文件权限；
- Docker 干净环境构建；
- Compose 默认配置安全性；
- 前端 TypeScript 和生产构建；
- JWT 过期和 token 泄露后的失效策略。

## 7. 发布前建议顺序

### 第一阶段：安全阻塞项

1. 删除 `docker-compose.yml` 中的固定默认 JWT 密钥；
2. 验证并加固 session/session string 文件权限；
3. 重新评估 WebSocket query token 方案；
4. 限制日志读取大小并加固日志路径；
5. 避免把内部异常文本直接返回客户端。

### 第二阶段：执行正确性

6. 为任务执行增加原子并发锁；
7. 统一跨进程账号级互斥；
8. 引入持久化 `run_id` 和任务状态机；
9. 明确单用户模型，或补充资源所有权；
10. 处理服务重启和任务异常恢复。

### 第三阶段：构建与质量门禁

11. 修复 16 个 Ruff E402 错误；
12. 修复或重新生成有效的 `frontend/package-lock.json`；
13. 在 Node 22 干净环境完成前端构建；
14. 在 Docker 干净环境执行完整构建和启动冒烟；
15. 将后端测试、Ruff 和前端构建纳入 CI。

## 8. 总体评价

本次升级的优点：

- 后端安全校验明显增强；
- 登录、TOTP、限流和审计日志流程较完整；
- 166 个 Python 测试全部通过；
- 签到任务、账号管理、日志、WebSocket 和 Docker 前端托管功能集成较全面；
- Telegram topic/thread、配置迁移和自动化规则已有较好的测试基础。

但发布前必须优先解决：

1. 默认 JWT 密钥问题；
2. 前端 lockfile 和 Docker 构建验证问题；
3. Ruff 静态检查失败；
4. 任务并发和跨进程执行问题；
5. session string 和日志文件的敏感数据保护问题。

在上述问题处理完成前，建议将当前版本定位为**测试版或升级候选版**，不建议直接用于暴露在公网的生产环境。
