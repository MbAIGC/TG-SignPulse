# TG-SignPulse 第三轮 Review

> Review 对象：`doc/fix-plan-2026-08-30.md` 所列修复项及其对应代码、测试、CI 和部署文档。
>
> Review 结论：**本轮修复计划中的发布前整改项已基本闭环，核心安全、并发、构建和上游 0.9 兼容问题均已有实现与测试支撑，当前版本具备发布候选版本条件。**

## 1. 本轮验证结果

### 后端测试

执行：

```bash
source .venv/bin/activate
python -m pytest -vv tests/
```

结果：

```text
199 passed in 16.82s
```

### 静态检查

执行：

```bash
source .venv/bin/activate
python -m ruff check backend/ tg_signer/
```

结果：

```text
All checks passed!
```

### 前端构建

执行：

```bash
npm ci --include=dev --no-audit --no-fund
npm run build
```

结果：

- `vue-tsc` 通过；
- Vite 生产构建通过；
- PWA `generateSW` 通过；
- Service Worker 和 Workbox 文件生成成功；
- `package-lock.json` 与 `package.json` 根依赖一致。

## 2. 修复计划闭环情况

### 2.1 第一档：正确性和日志安全

以下项目已确认修复：

- `sign_once` 只清理当前 RouteKey 的消息缓存；
- `close_client_by_name` 会继续处理基础 key 和 `::memory` key；
- V1 → V3 配置迁移支持递归链路；
- 未知配置返回可读错误；
- 日志清理限制在日志目录内；
- 拒绝日志符号链接和非普通文件；
- 大日志采用尾读并限制读取大小；
- 返回日志截断标记。

对应回归测试已覆盖多 chat、客户端 key、配置迁移和日志安全场景。

### 2.2 第二档：安全加固

以下项目已确认修复：

- Docker Compose 删除固定弱 JWT 密钥默认值；
- 显式配置弱密钥或少于 32 字符的密钥会拒绝启动；
- 未配置密钥时自动生成并持久化；
- 自动生成的密钥文件设置 `0600`；
- session string 文件设置 `0600`；
- session 目录设置 `0700`；
- WebSocket 改为首帧 token 认证；
- 未认证连接在 10 秒后关闭；
- 未知异常不再直接把 `str(e)` 返回客户端；
- 服务端记录完整异常，客户端返回稳定错误信息。

### 2.3 第三档：架构和发布前整改

以下项目已确认落地：

- DB task 的 `run_task_once` 已接入统一 `AccountLock`；
- 锁覆盖创建运行记录、启动子进程和等待执行完成的完整区间；
- 跨进程锁使用 `LOCK_NB` 轮询；
- 支持 `ACCOUNT_LOCK_TIMEOUT`；
- 锁等待期间支持协程取消；
- 锁超时会写入失败记录，不再静默跳过；
- DB task 和 SignTask 均记录 `run_id`；
- `TaskLog` 增加 `worker_id` 和 `run_id` 字段；
- 已增加幂等 schema 补列逻辑；
- SignTask 运行状态支持内存缓存、磁盘持久化和重启恢复；
- 重启前的 running 状态会转为 cancelled；
- README 和 README_EN 已声明单用户模式；
- Ruff 检查已纳入 CI；
- 前端构建已纳入 CI；
- 已建立上游同步裁决表和 adapter 分层目标；
- Docker Compose 默认开启跨进程账号锁；
- 启动日志会打印账号锁模式；
- 已补充 DB task 锁超时和跨进程锁取消测试；
- `glob` 依赖已完成审计，当前 npm audit 结果为 0 漏洞。

## 3. 上一轮复审问题的处理结果

### 3.1 DB task 未接入账号级锁

已通过 `_run_task_once_locked` 解决。DB task 现在会按照账号名获取与 SignTask、Telegram 服务共享的 `AccountLock`。

### 3.2 文件锁无取消和超时

已改为非阻塞 `LOCK_NB` 轮询，增加：

- `ACCOUNT_LOCK_TIMEOUT`；
- `AccountLockTimeout`；
- 协程取消处理；
- 获取失败时自动释放进程内锁。

### 3.3 文档中的弱密钥示例

已将主要部署文档改为随机密钥示例：

```bash
openssl rand -hex 32
```

`.env.example` 中的 `APP_SECRET_KEY` 已改为空值并增加必填说明。

### 3.4 CI 前端依赖和环境配置

CI 已改为：

```yaml
npm ci --include=dev --no-audit --no-fund
```

并删除了 Production build 阶段的 `NODE_ENV=development` 设置。

## 4. 当前仍需注意的非阻塞事项

### 4.1 上游同步裁决表中仍有过时示例

`doc/upstream-sync-decision-table.md` 中仍有旧的：

```text
NODE_ENV=development npm run build
```

当前 CI 已改为显式安装 dev dependencies 并直接执行生产构建，建议同步更新该文档示例，避免后续维护人员照抄旧命令。

### 4.2 Web 调度仍保留 CLI 子进程轨道

当前通过 `AccountLock` 已实现 DB task 与 SignTask 的账号级互斥，因此短期风险已得到控制。但 Web 调度仍然通过 `tg-signer` 子进程执行部分任务。

长期建议：

- 逐步统一到进程内 service；
- 统一客户端生命周期；
- 统一日志、run_id 和取消语义；
- 减少子进程启动成本和跨进程状态同步。

该项属于行为级架构重构，不阻塞当前发布候选版本。

### 4.3 生产环境仍应执行真实部署验证

代码级测试和前端构建已经通过，但正式发布前仍应在目标环境完成：

- Docker 干净环境构建；
- Docker Compose 启动冒烟；
- 实际 Telegram session 登录；
- 多账号并发任务；
- 多 worker 部署；
- `/data` 权限验证；
- 反向代理 WebSocket 连接验证；
- 服务重启后的任务状态恢复；
- 长时间随机时间段任务。

## 5. 发布判断

### 已达到发布候选条件的部分

- Python 测试全部通过；
- 后端和核心库 Ruff 检查通过；
- 前端 TypeScript 检查和 Vite 构建通过；
- PWA 构建通过；
- 上游 0.9 RouteKey 和配置迁移问题已覆盖；
- 关键日志和路径安全问题已覆盖；
- JWT、WebSocket、session string 安全问题已加固；
- 账号级跨进程锁和运行状态已补齐；
- CI 已包含后端检查和前端构建门禁。

### 最终结论

> **修复计划中标记为发布前整改的项目已完成，当前版本可进入 Release Candidate 阶段。**

建议正式发布前再执行一次真实环境验证，并将以下项目作为后续迭代：

1. 更新 `doc/upstream-sync-decision-table.md` 中的旧构建命令；
2. 逐步移除 Web 调度对子进程 CLI 的依赖；
3. 统一 DB task 和 SignTask 的持久化状态模型；
4. 将面板历史记录完全统一到 SQLite 主存储；
5. 增加多 worker 和真实 Telegram 环境回归测试。
