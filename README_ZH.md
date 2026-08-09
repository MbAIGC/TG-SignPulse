<h1 align="center">TG-SignPulse</h1>

<p align="center">
  <strong>⚠️ 本项目已归档，不再维护 ⚠️</strong>
</p>

<p align="center">
  <a href="README.md">English</a>
</p>

---

> 📝 维护上下文、优化历史与已知问题请见 [PROJECT_NOTES.md](PROJECT_NOTES.md)。

## 项目说明

TG-SignPulse 是一个 **AI Vibe Coding 技术学习项目**，用于探索和实践以下技术栈的整合方式：

- 前后端分离架构（Vue 3 + FastAPI）
- 现代 Python 异步编程模式
- AI/LLM API 集成（OpenAI 兼容接口调用）
- 任务调度系统设计（APScheduler）
- Web 认证方案（JWT + TOTP 2FA）

本项目是作者在学习 AI 辅助编程（Vibe Coding）过程中的练手作品，旨在通过一个完整的全栈项目来实践 AI 驱动的开发流程。项目代码主要由 AI 辅助生成，用于展示 AI 编程工具在实际项目中的应用效果。

---

## 项目状态

> 🚫 **本项目已停止维护，不再更新。**
>
> - 不提供预构建镜像或任何形式的分发
> - 不接受新的 Issue 或 Pull Request
> - 代码仅供技术学习参考

---

## 技术栈

本项目涉及的技术栈，供学习参考：

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3、Vue Router、Pinia、Tailwind CSS 4、Vite |
| 后端 | FastAPI、Uvicorn、SQLAlchemy、SQLite、APScheduler |
| 认证 | JWT、TOTP 2FA、bcrypt |
| AI 集成 | OpenAI SDK（API 调用示例） |
| 第三方 API | Pyrogram（Telegram MTProto 协议学习） |

---

## 学习要点

本项目可作为以下方向的学习参考：

1. **全栈项目结构** — 前后端分离的项目组织方式
2. **异步 Python** — FastAPI + asyncio 的实际应用
3. **任务调度** — APScheduler 在 Web 应用中的集成
4. **AI API 调用** — OpenAI 兼容接口的封装与使用
5. **认证系统** — JWT + 2FA 的实现方式
6. **状态管理** — Pinia 在 Vue 3 中的使用模式

---

## 本地运行

环境要求：Python >= 3.10、Node.js >= 18。

```bash
# 1. 后端
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # 按需设置 APP_SECRET_KEY / APP_DATA_DIR
uvicorn backend.main:app --host 127.0.0.1 --port 8080

# 2. 前端（另开一个终端）
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173，/api 代理到 :8080

# 生产构建（产物由后端从 frontend/dist 托管）
npm run build
```

运行测试：

```bash
pip install pytest pytest-asyncio
pytest tests -q
```

注意事项：

- 首次启动会自动创建 `admin` 账号。可预先设置 `ADMIN_PASSWORD`，否则初始密码
  会写入 `<APP_DATA_DIR>/.admin_bootstrap_password`，请及时修改。
- 若后端在容器环境中启动卡住（uvloop 不兼容），请用 `--loop asyncio` 启动 uvicorn。
- `build` 脚本设置了 `NODE_OPTIONS=--experimental-global-webcrypto`，保证 PWA/
  workbox 构建在 Node 18 与 Node 20+ 下都能工作。

## Docker / Docker Compose 部署

仓库自带多阶段 [Dockerfile](Dockerfile) 与 [docker-compose.yml](docker-compose.yml)。
GitHub Actions workflow（[.github/workflows/docker-build-push.yml](.github/workflows/docker-build-push.yml)）
会自动构建镜像并推送到 GHCR：

- 推送到 `main` 分支 → `ghcr.io/<owner>/tg-signpulse:latest`
- 推送 `v1.2.3` 之类的 tag → `ghcr.io/<owner>/tg-signpulse:1.2.3` 与 `:v1.2.3`
- 也可在 Actions 页面手动触发（workflow_dispatch）

当前镜像仅构建 `linux/amd64` 架构。

使用 Docker Compose 部署：

```yaml
services:
  app:
    image: ghcr.io/mbaigc/tg-signpulse:latest
    container_name: tg-signpulse
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - TZ=Asia/Shanghai
      - APP_SECRET_KEY=your_secret_key
```

```bash
docker compose up -d
```

首次启动会自动创建 `admin` 账号：可在环境变量中设置 `ADMIN_PASSWORD`，或读取
`./data/.admin_bootstrap_password`（即 `./data` 卷内的初始密码）并在登录后修改。
容器内置了基于 `/healthz` 的健康检查。

## 近期优化

- 将 FastAPI 废弃的 `@app.on_event` 启动/关闭逻辑迁移为 `lifespan`；
  合并重复的 `/health`、`/healthz` 接口。
- 把仓库根目录的 `jose` / `pyotp` 影子包迁移为显式的 `backend.vendor` 模块，
  并移除 `python-jose`、`pyotp` 依赖，避免遮蔽真实三方包。
- 用 `bcrypt` 直接替换停止维护的 `passlib`（旧 `$2b$` 哈希仍然可用）。
- 调度器日志由 `print` 改为结构化 `logging`。
- 前端静态目录可配置（`APP_WEB_DIR`，默认 `frontend/dist`），移除过时的
  Next.js `/_next` 挂载。
- 端口/配置对齐：后端默认 8080、Vite 开发服务器 5173，CORS 与开发重定向默认值
  保持一致。
- 前端工具链兼容 Node 18：Vite 6.x、`@vitejs/plugin-vue` 5.x、`vue-router` 4.x、
  Tailwind 4.1.x（原锁定 Vite 8 / Vue Router 5 要求 Node 20.19+）。
- 新增单元测试：密码哈希、JWT、TOTP、配置。
- 新增 Docker 打包（多阶段构建，前端产物由后端托管）与 GHCR 推送 workflow，
  与 docker-compose 部署方式保持一致。
- 修复从 `.session` 文件导出会话字符串的问题：旧版 `"1"+base64` 格式无法被
  kurigram 2.2.x 解码，会导致执行签到任务时报
  "Invalid base64-encoded string" 错误。现在导出使用当前格式（含 api_id、
  无版本前缀），并会自动重建旧的坏缓存。
- 修复内存会话模式下的 `Failed to preheat chat_id` 错误：仅凭数字 chat_id
  无法解析的会话（私有超级群/频道、缺失 `-100` 前缀的历史配置等），现在会
  先扫描对话列表并把目标会话预热进 peer 缓存，再执行任务动作。

---

## 免责声明

- 本项目仅用于 AI 编程技术学习与交流，不鼓励也不支持任何自动化滥用行为
- 作者不对任何人使用本代码产生的后果负责
- 本项目不提供任何形式的技术支持或部署服务
- 代码中涉及的第三方 API 调用仅作为技术示例，使用者需自行遵守相关服务条款

---

## 致谢

本项目的 Telegram 协议交互部分参考了 [tg-signer](https://github.com/amchii/tg-signer) by [amchii](https://github.com/amchii)。

---

## License

[BSD-3-Clause](LICENSE)
