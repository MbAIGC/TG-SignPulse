# 部署验证清单（doc/第三轮GPT-review.md 4.3）

> 目标：正式发布前在干净/目标环境完成真实部署验证。
> 代码级可验证部分已在本环境执行；需真实 Telegram/公网环境的项明确标注，
> 不可在沙箱内完成的以「🔶 需目标环境」标出。

## 1. 本环境已完成（代码级验证）

| 验证项 | 命令/做法 | 结果 |
|---|---|---|
| 后端测试 | `pytest tests` | ✅ 210 passed |
| 静态检查 | `ruff check backend/ tg_signer/ tests/` | ✅ 全绿 |
| 前端构建 | `npm ci --include=dev --no-audit --no-fund && npm run build` | ✅ vue-tsc + vite + PWA |
| 多 worker 互斥（跨进程） | `tests/test_multi_worker.py`（双 worker 子进程并发同账号） | ✅ 执行区间不重叠、worker_id 可区分 |
| 跨进程文件锁 | `tests/test_account_locks.py`（flock/超时/取消） | ✅ |
| DB task 复用进程内执行 | `tests/test_tasks_account_lock.py`、`test_db_task_state_unify.py` | ✅ 锁复入规避、run_id 对齐状态机 |
| 历史 SQLite 主存储 | `tests/test_run_history_store.py` | ✅ 写读/截断/删除/JSON 兼容回退 |
| Docker 干净构建 | `docker build -t tg-signpulse:verify .`（DOCKER_CONFIG 指向可写目录） | ✅ EXIT=0 |
| Docker 启动冒烟 | `docker run` + 挂载 `/data` + `ACCOUNT_LOCK_FILE=1` | ✅ `/healthz` 200、SQLite WAL 正常、sessions 0700 |
| 重启恢复 | `docker restart` 后再次探测 | ✅ 重启后 `/healthz` 200、容器 `healthy` |
| 密钥权限 | 不设 `APP_SECRET_KEY` 启动容器 | ✅ 自动生成密钥 `0600` |
| API 认证保护 | 未带 token 访问 `/api/accounts`、`/api/tasks` | ✅ 401 |

## 2. 需目标环境执行的验证（🔶）

以下项需要真实 Telegram 凭据、公网可达性或真实部署拓扑，沙箱内无法完成，
发布前请在目标环境逐项执行并回填：

| 验证项 | 具体步骤 | 预期 |
|---|---|---|
| 实际 Telegram session 登录 | 用真实账号 `tg-signer login`（或面板扫码） | 登录成功、session 可复用 |
| 真实签到任务 | 配置真实任务并手动/定时运行 | 成功签到且写入 SQLite 历史 |
| 多账号并发任务 | 两个账号同时触发任务 | 无交叉、各自互斥、历史分账号 |
| 多 worker 部署 | 双进程/双容器同挂载 `/data` 运行 | 账号级互斥、无 session 锁冲突 |
| `/data` 权限验证 | 容器以非 root 或挂载宿主机目录运行 | 可读写、密钥文件 0600 |
| 反向代理 WebSocket | nginx/caddy 转发 `/api/sign-tasks/ws/*` | 首帧 token 认证后实时日志推送 |
| 服务重启后状态恢复 | 任务运行中 kill 容器再启动 | 磁盘 run-status 恢复、running 转 cancelled |
| 长时间随机时间段任务 | `execution_mode=range` 跨小时运行 | 随机等待正常、无超时 |
| JWT/密钥 | 设置强 `APP_SECRET_KEY` 启动 | 拒绝弱密钥；自动生成 0600 |

## 3. 本环境 Docker 验证记录

| 步骤 | 命令 | 结果 |
|---|---|---|
| 拉取基础镜像 | `docker pull node:22-alpine` | ✅ 网络可用 |
| 干净构建 | `docker build -t tg-signpulse:verify .` | ✅ EXIT=0（frontend/backend/runtime 三阶段全过） |
| 容器冒烟 | `docker run -d -p 18080:8080 -v <data>:/data -e APP_SECRET_KEY=... -e ACCOUNT_LOCK_FILE=1` | ✅ `/healthz` 200；`db.sqlite`/WAL 生成；`sessions` 0700 |
| 重启恢复 | `docker restart` | ✅ 重启后 `/healthz` 200，容器状态 `healthy` |
| 自动密钥 0600 | 不设 `APP_SECRET_KEY` 再启动 | ✅ `.app_secret_key` 权限 `600` |
| 认证保护 | 未带 token 请求 `/api/accounts`、`/api/tasks` | ✅ 401 |

> 沙箱备注：Docker CLI 配置目录 `/root/.docker` 只读，需
> `DOCKER_CONFIG=<workspace 可写目录>` 执行；构建与冒烟已在本环境完成，
> 多 worker 同挂载 `/data`、真实 Telegram、反向代理 WebSocket 仍以目标环境为准。
