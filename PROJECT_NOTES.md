# TG-SignPulse 项目笔记（维护记录）

> 本文档用于沉淀项目上下文、优化历史与已知问题。下次需要继续优化时，
> 先读本文件，再结合代码定位。

## 1. 项目概况

- 类型：Telegram 自动化签到面板（Web 管理 + 定时任务 + 关键词监听）
- 技术栈：
  - 后端：Python 3.10-3.13、FastAPI、SQLAlchemy 2 + SQLite、APScheduler、kurigram 2.2.7（pyrogram fork）
  - 前端：Vue 3、Vue Router 5、Pinia、Tailwind CSS 4、Vite 8、PWA
  - 认证：JWT（HS256，自研 vendored 实现）+ TOTP 2FA + bcrypt
- 原仓库（已归档）：https://github.com/akasls/TG-SignPulse
- 当前维护仓库：https://github.com/MbAIGC/TG-SignPulse
- 部署镜像：`ghcr.io/mbaigc/tg-signpulse:latest`（公开，linux/amd64）

## 2. 部署方式

```bash
docker compose up -d          # 前端产物由后端托管，单容器
docker compose pull           # 更新到最新镜像
docker compose logs -f        # 查看日志
```

- 数据目录：`./data:/data`（sessions、signs、db.sqlite、日志都在里面）
- 首次启动自动创建 `admin` 账号：设 `ADMIN_PASSWORD` 或读
  `/data/.admin_bootstrap_password`
- 环境变量：`APP_SECRET_KEY`（必设）、`TZ`、`APP_PORT`、`APP_DATA_DIR`、
  `FRONTEND_DEV_SERVER_URL`、`APP_WEB_DIR`、`TG_API_ID`/`TG_API_HASH`、
  `TG_SESSION_MODE`、`TG_GLOBAL_CONCURRENCY` 等

## 3. 优化历史

### 2026-08-09 第一轮：代码现代化 + 构建修复

- 提交 `a65dba9`（含后续多轮修改）
- FastAPI：`@app.on_event` → `lifespan`；合并 `/health`、`/healthz`
- 根目录 `jose/`、`pyotp.py` 影子包 → 迁移为 `backend/vendor/`，
  移除 `python-jose`、`pyotp` 依赖
- `passlib` → 直接 `bcrypt`（旧 `$2b$` 哈希兼容）
- scheduler 的 `print` → `logging`
- 静态目录可配置（`APP_WEB_DIR`，默认 `frontend/dist`）；删除 Next.js `/_next` 死代码
- 端口对齐：后端默认 8080、Vite 5173

### 前端工具链（Node 18 兼容，后续已撤销）

- Vite 8 → 6.x；`@vitejs/plugin-vue` 6 → 5.x；`vue-router` 5 → 4.x；
  Tailwind 4.3 → 4.1.18（oxide 原生绑定支持 Node 18）
- `build` 脚本加 `cross-env NODE_OPTIONS=--experimental-global-webcrypto`
  （workbox/serialize-javascript 在 Node 18 下需要全局 crypto）

> ⚠️ 该兼容方案已被 2026-08-09 的“依赖升级到最新”撤销（见下方第 5 条）。

### Docker + GHCR CI

- 多阶段 `Dockerfile`（node:20-alpine 构建前端 + python:3.11-slim 装依赖/运行）
- `.github/workflows/docker-build-push.yml`：push main → `:latest`；
  tag `v*` → 版本号；支持手动触发；镜像名自动小写
- `docker-compose.yml`：`ghcr.io/mbaigc/tg-signpulse:latest`

### Bug 修复（重要）

1. **会话字符串 base64 报错**（提交 `65984c4`）
   - 症状：执行任务报 `Invalid base64-encoded string: number of data characters (357)`
   - 原因：`_export_session_string_from_file` 生成旧 v1 格式
     （`"1" + base64(267B)` = 357 字符），kurigram 2.2.x 无法解码
   - 修复：改用当前格式 `>BI?256sQ?`（含 api_id、无前缀，362 字符）；
     `load_session_string_file` 自动识别并重建旧坏缓存
   - 回归测试：`tests/test_tg_session.py`
2. **任务预热 peer 失败**（提交 `58d9599`）
   - 症状：`Failed to preheat chat_id xxx: CHAT_ID_INVALID / PEER_ID_INVALID`
   - 原因：内存会话 peer 缓存为空，纯数字 chat_id 解析需要 access_hash
   - 修复：预热失败时遍历 `get_dialogs()` 把目标会话拉回缓存再解析，
     兼容缺失 `-`/`-100` 前缀的历史 ID
3. **日志措辞误导**（提交 `46e39ce`）
   - `开始登录...` → `正在连接账号会话（无需重新登录）...` + 连接成功账号名
4. **文档调整**（提交 `ae46cba` 后续）
   - 新增 `PROJECT_NOTES.md` 维护记录；README 改为中文默认
     （`README.md`），英文同步为 `README_EN.md`，删除 `README_ZH.md`；
     结构参照 fork [loochenx/TG-SignPulse](https://github.com/loochenx/TG-SignPulse)，
     署名 Codex + Deepseek
5. **依赖升级到最新**（2026-08-09）
   - 前端：Vite 8.2、`@vitejs/plugin-vue` 6.0、`vue-router` 5.2、
     Tailwind 4.3.3、`vue-tsc` 3.3、TypeScript 6.0（未上 7.x，Go 版兼容性未就绪）；
     `lucide-vue-next` → `@lucide/vue`（GitHub 品牌图标被 lucide 移除，
     改用 `GitBranch`）；要求 Node `^20.19 || >=22.12`，Docker 构建镜像
     改为 `node:22-alpine`，删除 cross-env crypto 兼容
   - 后端：`pydantic<2` → `pydantic>=2,<3`（实测 2.13.4），代码原有
     v1/v2 双兼容层直接生效，21 个测试与启动冒烟全部通过
6. **pydantic v2 运行期回归修复**
   - 症状：任务执行报 `KeyError: <chat_id>`（sign_once 里
     `self.context.sign_chats[chat.chat_id].append(chat)`）
   - 原因：`UserSignerWorkerContext` 用 `defaultdict(list)` 构造但字段注解为
     `dict`；v1 保留 defaultdict 实例，v2 校验时转为普通 dict，自动建键失效
   - 修复：不再依赖 defaultdict，构造传普通 dict，所有下标写入改用
     `setdefault(...)`；新增回归测试 `tests/test_pydantic_v2_context.py`
7. **每日签到次数（1-5 次/天）**
   - 任务配置新增 `daily_times`（默认 1，1-5）；固定模式按 `index/总数*1440min`
     偏移分散到一天内，range 模式把时间窗口等分为 N 段、每段随机执行一次；
     Job ID 变为 `sign-<账号>-<任务>#<i>`，兼容移除旧 ID
   - 涉及：`backend/scheduler`、`backend/services/sign_tasks.py`、
     `backend/api/routes/sign_tasks_v2.py`、前端 TaskForm/i18n；
     测试 `tests/test_scheduler.py`

## 4. 已知问题 / 环境注意

- **uvloop 卡启动**：本容器/部分环境 uvloop 会导致 uvicorn 启动挂起，
  本地调试用 `uvicorn backend.main:app --loop asyncio`
- **Starlette TestClient 在本沙箱不可用**：anyio blocking portal 会死锁，
  接口测试需启动真实 uvicorn + httpx（用 `--loop asyncio`）
- ~~lucide-vue-next 已废弃~~ → 已迁移 `@lucide/vue`
- ~~pydantic 锁定 v1~~ → 已升级 v2（2.13.4）
- **镜像仅 amd64**：如需 arm64，workflow 的 `platforms` 加 `linux/arm64`
  并验证依赖 wheel（kurigram 纯 Python，uvloop/httptools/pillow/bcrypt 均有 arm64 wheel）
- **根目录 vitepress 残留已清理**：原 `package.json`/`package-lock.json`
  只有 docs 脚本但无 `docs/` 目录

## 5. 本地开发

```bash
# 后端
python -m venv .venv && source .venv/bin/activate
pip install -e .
uvicorn backend.main:app --host 127.0.0.1 --port 8080 --loop asyncio

# 前端（另开终端）
cd frontend && npm install && npm run dev

# 测试 / Lint
pytest tests -q
ruff check backend tests
```

## 6. 待办 / 下一步优化候选

- [x] 迁移 `lucide-vue-next` → `@lucide/vue`
- [x] pydantic v1 → v2（2.13.4）
- [ ] CI 增加 `linux/arm64` 多架构构建
- [ ] 补充服务层/接口层测试（沙箱内 TestClient 受限，可用真实 uvicorn + httpx）
- [ ] 关键词监听（keyword_monitor）代码量大（1700+ 行），建议后续专项审计
- [ ] `backend/cli/`（signer/tasks CLI）未覆盖测试
- [ ] 前端 i18n 资源整理（useI18n 内联中文串）
