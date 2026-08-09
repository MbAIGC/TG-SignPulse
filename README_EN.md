# TG-SignPulse

> A Telegram multi-account automation panel for check-ins, action workflows, and keyword monitoring.

[中文说明](README.md) · [Project Notes](PROJECT_NOTES.md) · [Health Checks](#health-checks) · [Changelog](#changelog)

TG-SignPulse is a Telegram automation panel. Manage multiple accounts, configure auto check-in tasks, and let them run on a fixed schedule from a web UI.

> ✨ This repository is continuously optimized and maintained with the help of **Codex + Deepseek**.

## What Is This Project For?

- Manage multiple Telegram accounts in one place (phone-code or QR-code login)
- Automate check-ins, scheduled messages, and button clicks with fixed-time or random-range schedules
- 8 action types, including AI vision, AI math solving, and keyword monitoring
- Run check-ins inside specific Telegram group topics (Thread/Topic)
- Real-time WebSocket log streaming with per-run flow details and latest bot replies
- Global proxy, failure notifications, keyword monitoring, and push notifications
- Built to run reliably on a VPS for long-term automation

## Highlights

- **Multi-account management**: phone-code or QR-code login, per-account proxy support
- **8 action types**: Send Text, Send Dice, Click Button, AI Vision → Click Button, AI Vision → Send Text, AI Calculate → Send Text, AI Calculate → Click Button, Keyword Monitor
- **Two scheduling modes**: fixed CRON time or randomized execution within a time window
- **Topic check-ins**: send and filter replies by specific Thread/Topic in Telegram forum groups
- **Notifications**: task failures, invalid sessions, and login alerts; keyword matches support Telegram Bot, Bark, or a custom URL
- **Real-time logs**: WebSocket live log streaming, history auto-retained for 3 days
- **Panel security**: JWT auth + TOTP two-factor authentication, per-task failure-notification toggle
- **Docker-first deployment**: Docker / Docker Compose ready, persistent data directory

## Feature Map

| Area | Capability |
| --- | --- |
| Account management | Multi-account login (phone/QR), per-account proxy, status checks, re-login, TOTP 2FA |
| Task workflows | Fixed CRON / random-range schedules, 8 action types, action interval, auto-delete messages |
| Topic support | Send and filter replies by Telegram group `Thread ID` |
| Keyword monitoring | Contains / regex matching, push notification or continue the action sequence on match |
| Notifications | Global: task failure / invalid session / login; keyword match: Telegram Bot / Bark / custom URL |
| Operations | Docker deployment, persistent data directory, health checks, config version migration |

## Quick Start

### Beginner Deployment (3 Steps)

1. Install Docker
2. Run the container command below
3. Open `http://YOUR_SERVER_IP:8080` in a browser and log in with `admin`

Default credentials:

- Username: `admin`
- Password: generated on first startup; read `/data/.admin_bootstrap_password`,
  or preset it with the `ADMIN_PASSWORD` environment variable (recommended)

### One-command Deploy

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

If you use a reverse proxy, bind locally only:

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

## Data Directory & Permissions

- Default data directory: `/data` (sessions, signs, database, and logs)
- If `/data` is not writable, the app falls back to `/tmp/tg-signpulse` (non-persistent)
- Container troubleshooting:

```bash
id
ls -ld /data
touch /data/.probe && rm /data/.probe
```

## Common Environment Variables

- `APP_SECRET_KEY`: panel secret key (strongly recommended; auto-generated and persisted if unset)
- `ADMIN_PASSWORD`: initial password for the `admin` user (recommended; otherwise read
  `/data/.admin_bootstrap_password`)
- `APP_HOST` / `APP_PORT`: backend listen address and port (default `127.0.0.1:8080`;
  the container listens on `0.0.0.0:8080`)
- `APP_DATA_DIR`: custom data directory (higher priority than the panel setting)
- `APP_WEB_DIR`: frontend build output directory (default `<repo>/frontend/dist`)
- `FRONTEND_DEV_SERVER_URL`: frontend dev-server URL (default `http://127.0.0.1:5173`)
- `TZ`: timezone (default `Asia/Hong_Kong`)
- `TG_API_ID` / `TG_API_HASH`: Telegram API credentials (use your own in production;
  built-in demo values are used when unset)
- `TG_PROXY`: Telegram connection proxy; you can also configure a global proxy in the panel
- `TG_SESSION_MODE`: `file` (default) or `string`
- `TG_SESSION_NO_UPDATES`: set `1` to enable `no_updates` (`string` mode only)
- `TG_GLOBAL_CONCURRENCY`: global concurrency limit (default `1`)
- `APP_TOTP_VALID_WINDOW`: panel 2FA tolerance window

## Custom Data Directory

You can set the data directory in two ways:

1. Panel: `System Settings -> Global Sign-in Settings -> Data Directory`
2. Environment variable: `APP_DATA_DIR=/your/path`

Notes:

- Restart the backend after changing it
- Make sure the directory is writable and mounted as a persistent volume

## Local Development

- Python `>=3.10,<3.14` (3.11 / 3.12 recommended); Node.js `^20.19 || >=22.12`
- Convenience commands (see [Makefile](Makefile)):

```bash
make install     # install backend + frontend dependencies
make backend     # start backend at http://127.0.0.1:8080
make frontend    # start frontend at http://127.0.0.1:5173
make test        # run unit tests
make build       # build the frontend production bundle
```

## Health Checks

- `GET /healthz`: liveness check
- `GET /readyz`: readiness check

## Project Structure

```text
backend/      FastAPI backend and scheduler
tg_signer/    Telegram automation core library
frontend/     Vue 3 + Vite admin panel
tests/        unit tests
```

## Changelog

### 2026-08-09

- **Modernization**: FastAPI `lifespan`, unified health checks, vendored
  `backend/vendor` JWT/TOTP modules, direct bcrypt, structured scheduler logs,
  configurable static directory
- **Dependency upgrades**: Vite 8 / Vue Router 5 / Tailwind 4.3 / `@lucide/vue` /
  pydantic v2 (requires Node `^20.19 || >=22.12`)
- **Docker + GHCR CI**: multi-stage image, auto-push to `ghcr.io/mbaigc/tg-signpulse`
- **Session-string fix**: `.session` exports now use the kurigram-compatible
  format; stale legacy caches are rebuilt automatically
- **Peer preheat fix**: in-memory sessions preheat target chats by scanning
  dialogs; legacy IDs missing the `-100` prefix are handled
- Full record: [PROJECT_NOTES.md](PROJECT_NOTES.md)

## Acknowledgements

- Upstream: [akasls/TG-SignPulse](https://github.com/akasls/TG-SignPulse) (archived),
  which itself references [amchii/tg-signer](https://github.com/amchii/tg-signer).
  Thanks to both authors for their open-source work.
- README structure and deployment docs referenced:
  [loochenx/TG-SignPulse](https://github.com/loochenx/TG-SignPulse)
- Continuous optimization of this repository is assisted by **Codex + Deepseek**.

## License

[BSD-3-Clause](LICENSE)
