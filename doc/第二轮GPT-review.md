# TG-SignPulse 修复后复审结论

## 1. 总体结论

本轮修复质量明显提升。上一轮 Review 中列出的主要 P0/P1 问题大部分已经实际落地，并有对应测试覆盖：

- 后端测试由 166 项增加到 **194 项全部通过**；
- Ruff 静态检查通过；
- 前端在显式包含开发依赖后完成 `vue-tsc + Vite + PWA` 生产构建；
- 默认 JWT 弱密钥、日志路径/大小、WebSocket token、session string 权限、V1→V3 迁移、RouteKey 缓存清理、run_id 和状态持久化均已处理。

当前版本可以评价为：

> **核心安全问题基本修复，库层和面板层的主要兼容风险已得到控制，但数据库任务与进程内签到任务仍未完全共享账号级执行锁。**

暂不建议在未处理剩余问题前直接作为公网生产稳定版发布。

## 2. 验证结果

### 2.1 后端测试

执行：

```bash
source .venv/bin/activate
python -m pytest -vv tests/
```

结果：

```text
194 passed in 15.36s
```

新增并验证的场景包括：

- 账号锁并发和跨进程文件锁；
- RouteKey 只清理当前 chat；
- 基础客户端 key 和 `::memory` key 清理；
- V1→V3 递归配置迁移；
- 日志路径越界和大文件保护；
- 弱 JWT 密钥拒绝；
- session string 文件权限；
- WebSocket 首帧认证；
- 稳定错误响应；
- `run_id` 和运行状态恢复。

### 2.2 Ruff

执行：

```bash
source .venv/bin/activate
python -m ruff check .
```

结果：

```text
All checks passed!
```

### 2.3 前端

使用显式开发依赖安装后执行：

```bash
npm ci --include=dev --no-audit --no-fund
npm run build
```

结果：

- `vue-tsc` 通过；
- Vite 构建通过；
- PWA `generateSW` 通过；
- 生成 `dist/sw.js` 和 Workbox 文件。

普通 `npm ci` 在当前环境中曾因 npm 的 `omit=dev` 配置省略开发依赖，导致 `vue-tsc: not found`。因此 CI 应显式使用 `--include=dev`。

## 3. 发布前仍需处理的问题

### 3.1 P1：数据库任务仍未接入统一账号锁

涉及：

- `backend/services/tasks.py`；
- `backend/scheduler/__init__.py`；
- `backend/cli/tasks.py`。

数据库任务仍走：

```text
APScheduler
  -> run_task_once()
  -> async_run_task_cli()
  -> tg-signer 子进程
```

而进程内 SignTask 已使用 `AccountLock`。因此同一账号的 DB task 和 SignTask 仍可能同时执行，分别访问同一 Telegram session 或建立不同客户端。

当前 `run_task_once()` 只按 `task_id` 使用进程内布尔状态：

```python
if is_task_running(task.id):
    ...
_active_tasks[task.id] = True
```

它无法防止：

- 不同任务之间的同账号并发；
- 子进程与主进程之间的并发；
- 多 worker 之间的重复执行。

#### 建议

短期：在 `run_task_once()` 中按 `account.account_name` 获取并持有统一 `AccountLock`，覆盖创建运行记录、启动子进程、等待完成的完整区间。

同时，生产环境建议默认开启跨进程文件锁，例如：

```yaml
- ACCOUNT_LOCK_FILE=1
```

长期：让 Web 调度统一调用进程内 SignTask service，CLI 子进程只作为命令行兼容入口。

### 3.2 P1：跨进程文件锁缺少真正的取消/超时保护

位置：`backend/utils/account_locks.py:65-99`

当前使用：

```python
await asyncio.to_thread(self._acquire_file_lock)
```

底层 `flock` 是阻塞调用。虽然不会阻塞 asyncio event loop，但如果另一个进程长期持有文件锁：

- 当前协程可能无限等待；
- 外层任务取消不能真正取消线程中的阻塞 `flock`；
- 长期可能占用线程池资源；
- scheduler 任务可能卡在等待锁的阶段。

#### 建议

使用非阻塞 `LOCK_NB` + 轮询方式实现：

- 定期检查截止时间；
- 响应协程取消；
- 到期关闭 fd；
- 抛出明确的 `AccountLockTimeout`；
- 记录账号、任务和等待时长。

建议提供配置项：

```text
ACCOUNT_LOCK_TIMEOUT=30
```

### 3.3 P1：README 仍存在会被拒绝的弱密钥示例

位置：

- `README.md:67`；
- `README.md:91`；
- `README_EN.md:68`；
- `README_EN.md:92`。

仍有：

```bash
-e APP_SECRET_KEY=your_secret_key
```

但现在应用会拒绝该值启动，因此用户照抄文档会直接部署失败。

#### 建议

改为：

```bash
-e APP_SECRET_KEY="$(openssl rand -hex 32)"
```

或明确写成：

```text
APP_SECRET_KEY 必须替换为至少 32 字符的随机值。
```

`.env.example` 中的 `your_secret_key_here` 也建议改成空值并注明必填，避免误用。

### 3.4 P1：CI 前端安装应显式包含 dev dependencies

位置：`.github/workflows/lint-test-build.yml:55-59`

当前：

```yaml
run: npm ci --no-audit --no-fund
```

建议改为：

```yaml
run: npm ci --include=dev --no-audit --no-fund
```

另外，当前 Production build 步骤设置了：

```yaml
NODE_ENV: development
```

这会造成“生产构建”和“development 环境”的语义冲突。建议删除该环境变量，或者改用明确的 `VITE_*` 构建变量。

## 4. 已确认修复的问题

以下问题本轮已确认修复：

### 安全配置

- Docker Compose 不再注入固定 `your_secret_key`；
- 显式配置的弱密钥或少于 32 字符的密钥会拒绝启动；
- 未配置密钥时自动生成并持久化。

### WebSocket 认证

- JWT 不再出现在 WebSocket URL；
- 改为连接后的首帧发送；
- 10 秒内未认证或认证失败则关闭连接。

### 会话凭据

- session string 文件设置为 `0600`；
- session 目录设置为 `0700`；
- 导出缓存也使用安全写入逻辑。

### 日志安全

- 日志路径限制在日志目录内；
- 拒绝符号链接和非普通文件；
- 大日志使用尾读并限制最大读取大小；
- 返回 `truncated` 标记；
- 未知异常不再直接泄露给客户端。

### 上游 0.9 兼容

- `sign_once` 只清理当前 RouteKey；
- 客户端双 key 清理逻辑已修复；
- V1/V2/V3 配置迁移链已递归处理；
- `run_id` 已加入运行状态和日志记录；
- 运行状态支持磁盘持久化和重启恢复。

## 5. 其他观察项

### 5.1 自动生成 JWT 密钥写入失败时应更明显处理

`backend/core/config.py` 在自动生成密钥写入失败时仍会返回内存中的随机值。这样当前进程可以启动，但重启后密钥可能变化，导致所有旧 JWT 失效。

建议：

- 记录明确的 error；
- 生产模式下写入失败时拒绝启动；
- 或至少输出“密钥未持久化，重启后会失效”的警告；
- 对 `.app_secret_key` 设置 `0600`。

### 5.2 DB task 仍使用旧式内存布尔状态

当前状态模型仍是两套：

```text
SignTask: run_id + 内存/磁盘状态
DB Task: task_id + 进程内 bool
```

后续应统一为持久化状态机，至少包含：

- `run_id`；
- `state`；
- `started_at`；
- `finished_at`；
- `error`；
- `worker_id` 或进程标识。

### 5.3 跨进程锁默认关闭需要明确说明

`ACCOUNT_LOCK_FILE` 当前默认关闭，默认只提供进程内锁。建议：

- Docker 生产部署默认启用；
- 启动日志打印锁模式；
- 多 worker 且锁关闭时输出警告；
- README 明确单进程和多进程的区别。

### 5.4 前端依赖存在 deprecated 警告

构建时出现 `glob` deprecated 警告。构建未失败，但建议后续执行：

```bash
npm ls glob
npm audit --omit=dev
npm audit --include=dev
```

确认具体依赖链并评估升级方案。

## 6. 推荐整改顺序

### 发布前

1. `backend/services/tasks.py` 接入统一 `AccountLock`；
2. 生产环境默认开启跨进程账号锁；
3. 修复 `README.md` / `README_EN.md` 中的弱密钥示例；
4. CI 使用 `npm ci --include=dev`；
5. 移除 CI 中 `Production build + NODE_ENV=development` 的矛盾配置；
6. 为文件锁增加可取消/超时获取。

### 后续迭代

7. 统一 DB task 和 SignTask 的状态机；
8. 逐步移除 Web 调度对子进程 CLI 的依赖；
9. 统一后端历史记录到 SQLite 主存储；
10. 增加真实 Telegram 会话、多 worker、Docker 和长时间任务测试；
11. 处理自动生成密钥持久化失败；
12. 跟踪 `glob` 依赖安全告警。

## 7. 最终发布判断

当前版本：**接近可发布，但仍不建议直接作为公网生产稳定版。**

已达到的水平：

- 后端功能回归通过；
- 核心安全问题基本闭环；
- 上游 0.9 兼容性主要问题已修复；
- 前端生产构建已验证；
- 静态检查已通过。

剩余阻塞主要集中在任务执行架构：

> **DB task 的子进程执行与进程内 SignTask 尚未完全纳入同一账号级互斥模型。**

完成该项并修正文档/CI 后，可以再进行一次 Docker 干净环境、单进程和多 worker 的发布候选验证。
