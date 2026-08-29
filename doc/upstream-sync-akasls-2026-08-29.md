# 上游同步记录：akasls 分支安全加固吸收（2026-08-29）

> 本文档记录 2026-08-29 将上游 [akasls/TG-SignPulse](https://github.com/akasls/TG-SignPulse)
> 分支 2026-08-22 恢复更新后的一批提交选择性合并进本仓库（MbAIGC fork）的过程、
> 冲突裁决依据、发现的上下游 bug，以及合并后的行为变化与注意事项。
> 对应 branch：`sync-upstream`，合入后的变更日志见 README「更新日志」。

## 1. 背景与仓库关系

- 项目源头：[amchii/tg-signer](https://github.com/amchii/tg-signer)（2024-05 创建）。
- 本仓库（`MbAIGC/TG-SignPulse`）与 `akasls/TG-SignPulse` 是**两个平行分叉**，
  共同历史止于 `981aa6a`（2026-05-20），此后各自独立发展。
- akasls 分支于 **2026-08-22** 集中推送了一批安全加固提交（共 6 个，全部由 Ernest Badilla 提交）。
- 权衡结论：内容以真实漏洞修复为主、价值高，但**不建议整条 merge**（Docker/CI/前端
  改法与本地理念不同、冲突集中在核心安全文件），因此采取**选择性吸收（方案 A）**。

## 2. 合并范围

| 上游提交 | 主题 | 处理 |
|---|---|---|
| `18e1ffb` | 全量安全加固（45 文件，+1880/-347） | 选择性合并，冲突 7 处逐条裁决（见 §3） |
| `8f6e989` | auth 登录 token UnboundLocalError 修复 + 2FA 测试 | 完整吸收，无冲突 |
| `3f62271` | 生产环境禁用 Swagger/ReDoc/OpenAPI | 吸收（本地结构调整后应用） |
| `0906d45`/`c3ba716`/`d2a6c14` | Docker 构建修复、404 页面 + 国际化 | **跳过**（见 §5） |

吸收后的净增量：37 个文件，+1266/-329 行。

## 3. 冲突裁决记录（4 处核心冲突）

1. **`backend/core/auth.py`**：吸收上游 `_DUMMY_BCRYPT_HASH`（防用户名枚举时序攻击，
   密码不匹配时也走恒定时间 bcrypt 计算）；保留本地 vendor 化 pyotp/jose 导入
   （上游用直连依赖，本地在 `backend/vendor` 自研实现）。
2. **`backend/core/security.py`**：**整体保留本地版**。本地已完成 pydantic v2 升级，
   密码哈希已是原生 bcrypt 且带 72 字节截断（兼容 passlib 历史哈希），
   上游的 bcrypt 改动与之重复且删掉了截断处理，合并会引入**倒退**。
3. **`backend/main.py`**：本地是 `lifespan` 结构（较上游 `@app.on_event` 新），保留本地
   结构；移植上游 **serve_spa 路径穿越防护**（所有文件解析结果必须落在 web_dir 内）；
   shutdown 增加 `close_all_clients` + `trim_memory`（进程退出时释放 Telegram 连接，
   与本地「任务运行中不强杀客户端」的 572f771 决策不冲突：`_shutdown` 只在退出时执行）。
4. **`tg_signer/core.py`**：保留本地 `close_client_by_name` 的引用计数保护
   （refs>0 只摘缓存不强杀，见 572f771），合并上游 `asyncio.wait_for` 锁获取修复与
   `keys_to_clean`（含 ::memory 双 key）、`clear_client_cache`、`close_all_clients`
   重构；吸收 `ensure_user()`（上游 6 处调用点必需），保留本地 `login` 签名
   （`num_of_dialogs=20, print_chat=True`）。

## 4. 发现并修复的上游（及其继承的）BUG

1. **`disable_totp` 丢失 return（P1）**：上游 18e1ffb 在 `backend/api/routes/user.py` 中
   删掉了 `POST /api/users/totp/disable` 的 `return DisableTOTPResponse(...)`（函数落在
   新加的 `ResetUserTOTPRequest` 类前）。隐式返回 None 会让 FastAPI 响应校验失败 →
   该接口调用必现 HTTP 500。已恢复原 return。
2. **`configure_logger` 丢失 setLevel（P1）**：上游 18e1ffb 把
   `tg_signer/logger.py` 中的 `logger.setLevel(level_no)` 替换成了「关闭旧 handler」的
   循环，导致 logger 级别退回继承 root（默认 WARNING），INFO 级签到日志全部不落盘。
   已恢复 `logger.setLevel(level_no)`（保留其关闭旧 handler 的改进）。

## 5. 主动适配（非上游提交）

- **serve_spa 对文档端点 404 短路**：`/docs`、`/redoc`、`/openapi.json`、
  `/docs/oauth2-redirect` 直接 404。否则 SPA 兜底会把它们 307 重定向到前端开发服务器，
  而 TestClient 会把 307 再次打到应用自身形成重定向死循环（上游测试
  `test_fastapi_docs_endpoints_disabled` 因此必挂），且与「禁用文档端点」意图不符。

## 6. 未吸收部分及理由

- `Dockerfile` / `.dockerignore` / `frontend/package-lock.json`：本地已有自己的多阶段
  Dockerfile 与 GHCR CI（`docker-build-push.yml`），上游改法与本地并存会造成维护混乱。
- `.github/workflows/docker.yml`（上游新 CI）：同上，已 `git rm`。
- `d2a6c14` 前端 404 页面 + 国际化：本地前端刚完成一轮现代化（Vue3/Vite8），
  `useI18n.ts`、`router` 两边都改过，重复工作且冲突大。
- `0906d45`/`c3ba716` Docker 构建修复：不适用本地 Dockerfile。

## 7. 行为变化与注意事项

1. **账号名校验更严格**（`backend/utils/names.py`，上游加固）：现在拒绝 Windows 非法
   字符（`: * ? " < > |`、控制字符）、结尾点/空格、Windows 保留设备名
   （con/prn/aux/nul/com1..9/lpt1..9）。⚠️ 存量账号/任务配置若含这些字符，API 层将
   拒绝操作，需要先在文件系统层面重命名。
2. **CLI 默认不拉取对话**：`tg-signer run/run_once/monitor run` 与
   `python -m backend.cli.tasks` 的 `--num-of-dialogs` 默认从 50/20 改为 **0**
   （0 = 不拉取）。需要列出最近对话时显式传 `-n N`。
3. **TOTP 重置接口契约变更**：`POST /api/users/totp/reset` 现在要求请求体带
   `password` 且校验通过；`POST /api/auth/reset-totp` 要求服务端配置
   `APP_EMERGENCY_RESET_KEY` 环境变量并提供匹配的 `emergency_key`，否则 403，
   提示改用 `python -m backend.cli reset-totp <username>`（新增 CLI，含
   `reset-password`、`list-users` 子命令）。前端目前只调用 `/totp/disable`
   （契约未变），不受影响。
4. **登录 token 有效期**：`login` 移除硬编码 12h，改用配置默认值
   （`access_token_expire_hours`，默认仍为 12h），行为不变。
5. **`UserMonitor.run` 的 `num_of_dialogs` 参数不再生效**（上游改用 `ensure_user()`），
   属死参数，保留仅为兼容 CLI 签名。
6. **`GET /api/tasks-v2/chats`（get_account_chats）非会话失效类错误码 404 → 400**：
   仅会话失效仍返回 409（`ACCOUNT_SESSION_INVALID`），前端按 404 处理的历史逻辑
   可能需要同步调整（未改前端，仅记录）。
7. **SQLite PRAGMA 增强**：WAL + busy_timeout=30s + synchronous=NORMAL +
   cache_size/-temp_store，减小并发写锁概率。
8. **速率限制**：`get_client_identifier` 只信任合法 IP（防 X-Forwarded-For 伪造），
   增加过期桶自动清理。
9. **推送通道**：Bark/自定义 URL 只允许 http(s)，复用连接池（httpx 全局 AsyncClient）。

## 8. 测试与质量

- 新增上游 5 个安全测试文件（17 个用例）：路径穿越、任务日志沙箱、TOTP/2FA 流程、
  速率限制、账号存储并发。
- 全套 `pytest tests`：**19/19 通过**。
- ruff：F401/I001 已清零；其余 ~900 条为 ruff 0.16.5 相对项目预期 0.14.6 的
  默认规则集漂移（合并前基线即 928 条，非本合并引入）。

## 9. 同步与回退

- 分支：`sync-upstream`（基于 `main` 顶部 3 个 cherry-pick + 1 个跟进提交）。
- 合入：`git checkout main && git merge --no-ff sync-upstream`。
- 回退：任一提交均可 `git revert <sha>`（4 个提交相互独立）：
  - `3c54329` 安全加固合并（含 BUG 修复，建议保留）
  - `540f0a0` auth 修复 + 2FA 测试
  - `ff05f9b` 禁用文档端点
  - `9e61339` 文档端点 404 短路 + ruff 导入规范