# TG-SignPulse

> Telegram 多账号自动签到、消息动作编排与关键词监听面板。

[English README](README_EN.md) · [项目笔记](PROJECT_NOTES.md) · [健康检查](#健康检查) · [更新日志](#更新日志)

TG-SignPulse 是一个 Telegram 自动化管理面板。你可以在网页里管理多个账号，配置自动签到任务，并让任务按固定规则每天自动执行。

> ✨ 本仓库由 **Codex + Deepseek** 协助持续优化维护。

## 这个项目是做什么的？

- 统一管理多个 Telegram 账号（手机号验证码登录或二维码扫码登录）
- 自动签到、定时发消息、点击按钮，支持固定时间和随机时间段两种调度模式
- 8 种动作类型，含 AI 识图、AI 计算题、关键词监听
- 支持指定群组话题（Thread/Topic）执行签到
- 实时 WebSocket 日志流，可直接在网页查看执行过程和机器人最后回复
- 支持全局代理、失败通知、关键词监听与通知推送
- 适合 VPS 长期运行

## 项目亮点

- **多账号管理**：手机号 / 二维码两种方式登录，账号支持独立代理
- **8 种动作类型**：发送文本、发送骰子、点击按钮、AI 识图后点按钮、AI 识图后发文本、AI 计算后发文本、AI 计算后点按钮、关键词监听通知
- **两种调度模式**：固定 CRON 时间 或 时间窗口内随机执行
- **每日多次签到**：任务可设置每天执行 1-5 次（固定时间自动分散、时间范围自动分段）
- **话题签到**：支持 Telegram Forum 群组指定 Thread/Topic 内执行
- **通知推送**：任务失败、账号失效、登录通知；关键词命中支持 Telegram Bot、Bark、自定义 URL 三种渠道
- **实时日志**：WebSocket 实时推送执行日志，历史记录自动保留 3 天
- **面板安全**：JWT 认证 + TOTP 两步验证，支持单独关闭每个任务的失败通知
- **容器化部署**：Docker / Docker Compose 开箱即用，数据目录持久化

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 账号管理 | 多账号登录（手机号/二维码）、独立代理、状态检测、重新登录、TOTP 2FA |
| 任务编排 | 固定 CRON / 时间窗口随机执行，每日签到次数（1-5），8 种动作类型，动作间隔与自动删消息 |
| 话题支持 | 群组 `Thread ID` 级别的发送与回复过滤 |
| 关键词监听 | 包含/正则两种匹配，命中后推送通知或继续执行后续动作序列 |
| 推送通知 | 全局：任务失败/账号失效/登录；关键词命中：Telegram Bot / Bark / 自定义 URL |
| 运维能力 | Docker 部署、持久化数据目录、健康检查、配置版本自动迁移 |

## 快速开始

### 小白 3 步部署

1. 安装 Docker（服务器和本机都可）
2. 执行下面命令启动容器
3. 浏览器打开 `http://服务器IP:8080`，用 `admin` 登录

默认凭据：

- 账号：`admin`
- 密码：首次启动自动生成，读取 `/data/.admin_bootstrap_password`；
  或通过环境变量 `ADMIN_PASSWORD` 预设（推荐）

### 一条命令启动

```bash
docker run -d \
  --name tg-signpulse \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/data:/data \
  -e TZ=Asia/Shanghai \
  -e APP_SECRET_KEY=your_secret_key \
  ghcr.io/mbaigc/tg-signpulse:latest
```

如果你走反向代理（如 Nginx），可改成仅本机监听：

```bash
-p 127.0.0.1:8080:8080
```

### Docker Compose

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

## 数据目录与权限说明

- 默认数据目录：`/data`（sessions、signs、数据库、日志均在其中）
- 当 `/data` 不可写时，会自动降级到 `/tmp/tg-signpulse`（非持久化）
- 容器内排查命令：

```bash
id
ls -ld /data
touch /data/.probe && rm /data/.probe
```

## 常用环境变量（简版）

- `APP_SECRET_KEY`：面板密钥，强烈建议设置；未设置时自动生成并持久化
- `ADMIN_PASSWORD`：初次安装时 admin 账号的初始密码（推荐设置；未设置则
  读取 `/data/.admin_bootstrap_password`）
- `APP_HOST` / `APP_PORT`：后端监听地址与端口（默认 `127.0.0.1:8080`；
  容器内固定监听 `0.0.0.0:8080`）
- `APP_DATA_DIR`：自定义数据目录（优先级高于面板配置）
- `APP_WEB_DIR`：前端构建产物目录（默认 `<项目根>/frontend/dist`）
- `FRONTEND_DEV_SERVER_URL`：开发模式下前端服务器地址（默认 `http://127.0.0.1:5173`）
- `TZ`：时区（默认 `Asia/Hong_Kong`）
- `TG_API_ID` / `TG_API_HASH`：Telegram API 凭证（生产环境建议自行申请；
  未配置时使用内置演示值）
- `TG_PROXY`：Telegram 连接代理；也可在面板设置全局代理
- `TG_SESSION_MODE`：`file`（默认）或 `string`
- `TG_SESSION_NO_UPDATES`：设 `1` 启用 `no_updates`（仅 `string` 模式）
- `TG_GLOBAL_CONCURRENCY`：全局并发数（默认 `1`）
- `APP_TOTP_VALID_WINDOW`：面板 2FA 容错窗口

## 自定义数据目录

你可以通过两种方式设置数据目录：

1. 面板设置：`系统设置 -> 全局签到设置 -> 数据目录`
2. 环境变量：`APP_DATA_DIR=/your/path`

说明：

- 修改后建议重启后端服务生效
- 该目录请务必可写，并挂载持久化卷

## 本地开发

- Python `>=3.10,<3.14`（推荐 3.11 / 3.12）；Node.js `^20.19 || >=22.12`
- 常用命令（见 [Makefile](Makefile)）：

```bash
make install     # 安装后端 + 前端依赖
make backend     # 启动后端 http://127.0.0.1:8080
make frontend    # 启动前端 http://127.0.0.1:5173
make test        # 运行单元测试
make build       # 构建前端生产产物
```

## 健康检查

- `GET /healthz`：快速健康检查
- `GET /readyz`：服务就绪检查

## 项目结构

```text
backend/      FastAPI 后端与调度器
tg_signer/    Telegram 自动化核心库
frontend/     Vue 3 + Vite 管理面板
tests/        单元测试
```

## 更新日志

### 2026-08-29

- **同步上游安全加固**（akasls 分支 2026-08-22 更新，选择性吸收，详见
  [doc/upstream-sync-akasls-2026-08-29.md](doc/upstream-sync-akasls-2026-08-29.md)）：
  - **存储名安全**：账号/任务名校验拒绝 Windows 非法字符、结尾点/空格与
    保留设备名（con/nul/com1 等），修复跨平台路径穿越
  - **API 加固**：任务日志读取沙箱路径边界校验；生产环境禁用
    Swagger/ReDoc/OpenAPI 文档端点（`/docs`、`/openapi.json` 直接 404）
  - **认证加固**：TOTP 待绑定缓存并发锁、`/totp/disable` 修复（恢复缺失的
    return，修复上游引入的 500）、`/totp/reset` 强校验密码、应急重置需
    `APP_EMERGENCY_RESET_KEY`（或 `python -m backend.cli reset-totp`）
  - **基础设施**：账号配置 JSON 原子写 + 线程锁、速率限制 IP 伪造防护与
    过期清理、SQLite WAL/busy_timeout 调优、内存回收（trim_memory）与
    httpx 连接池复用
  - **修复上游 BUG**：`configure_logger` 丢失 `setLevel` 导致 INFO 日志不落盘；
    `disable_totp` 缺失 return 导致 HTTP 500
  - **新增**：5 个安全测试文件（19/19 通过）、`backend.cli` 管理员命令
    （reset-totp / reset-password / list-users）
  - 注意：CLI 默认不再拉取对话列表（`--num-of-dialogs` 默认 0）；
    账号名校验更严格，存量含非法字符的账号需先重命名

### 2026-08-17

- **修复 "Client has not been started yet"**：后台操作（账号删除/重登/状态检测、
  关键词监听重启等）不再强制关闭任务正在使用的共享 Telegram 客户端——
  `close_client_by_name` 改为尊重引用计数（使用中只摘除缓存、不强杀），
  进入共享实例时自动重连，登录前防御性重连

### 2026-08-09

- **每日签到次数**：任务可设置每天执行 1-5 次；固定时间按天均分到不同时刻，
  range 时间范围按窗口分段随机执行
- **代码现代化**：FastAPI `lifespan`、健康检查统一、自研 `backend/vendor`
  JWT/TOTP 模块、bcrypt 直连、调度器日志规范化、静态目录可配置
- **依赖升级到最新**：Vite 8 / Vue Router 5 / Tailwind 4.3 / `@lucide/vue` /
  pydantic v2（要求 Node `^20.19 || >=22.12`）
- **Docker + GHCR CI**：多阶段镜像、`ghcr.io/mbaigc/tg-signpulse` 自动推送
- **修复会话字符串错误**：`.session` 导出改用 kurigram 兼容格式，
  旧坏缓存自动重建
- **修复任务预热失败**：内存会话下通过对话列表预热 peer，
  兼容缺失 `-100` 前缀的历史配置
- 完整记录见 [PROJECT_NOTES.md](PROJECT_NOTES.md)

## 致谢

- 上游项目：[akasls/TG-SignPulse](https://github.com/akasls/TG-SignPulse)（已归档），
  其核心参考 [amchii/tg-signer](https://github.com/amchii/tg-signer)，感谢作者的开源工作
- README 结构与部署文档参考：[loochenx/TG-SignPulse](https://github.com/loochenx/TG-SignPulse)
- 本仓库的持续优化由 **Codex + Deepseek** 协助完成

## 许可

[BSD-3-Clause](LICENSE)
