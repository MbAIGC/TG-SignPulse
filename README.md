<h1 align="center">TG-SignPulse</h1>

<p align="center">
  <strong>⚠️ This project is archived and no longer maintained ⚠️</strong>
</p>

<p align="center">
  <a href="README_ZH.md">中文说明</a>
</p>

---

## About

TG-SignPulse is an **AI Vibe Coding learning project** created to explore and practice the integration of the following technology stacks:

- Frontend/backend separation architecture (Vue 3 + FastAPI)
- Modern Python async programming patterns
- AI/LLM API integration (OpenAI-compatible interface calls)
- Task scheduling system design (APScheduler)
- Web authentication (JWT + TOTP 2FA)

This project was built as a hands-on exercise during the author's exploration of AI-assisted programming (Vibe Coding). It demonstrates how AI coding tools can be applied in a full-stack project. The codebase was primarily generated with AI assistance, serving as a showcase of AI-driven development workflows.

---

## Project Status

> 🚫 **This project is discontinued and will not receive further updates.**
>
> - No pre-built images or distributions of any kind are provided
> - No new Issues or Pull Requests will be accepted
> - The code is available solely for technical learning reference

---

## Tech Stack

Technologies used in this project, for learning reference:

| Layer | Technology |
|-------|-----------|
| Frontend | Vue 3, Vue Router, Pinia, Tailwind CSS 4, Vite |
| Backend | FastAPI, Uvicorn, SQLAlchemy, SQLite, APScheduler |
| Auth | JWT, TOTP 2FA, bcrypt |
| AI Integration | OpenAI SDK (API call examples) |
| Third-party API | Pyrogram (Telegram MTProto protocol study) |

---

## Learning Highlights

This project can serve as a reference for:

1. **Full-stack project structure** — Organizing a frontend/backend separated application
2. **Async Python** — Practical use of FastAPI + asyncio
3. **Task scheduling** — Integrating APScheduler in a web application
4. **AI API calls** — Wrapping and using OpenAI-compatible interfaces
5. **Authentication system** — Implementing JWT + 2FA
6. **State management** — Using Pinia with Vue 3

---

## Local Development

Requirements: Python >= 3.10, Node.js >= 18.

```bash
# 1. Backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # set APP_SECRET_KEY / APP_DATA_DIR as needed
uvicorn backend.main:app --host 127.0.0.1 --port 8080

# 2. Frontend (separate terminal)
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173, proxies /api to :8080

# Production build (served by the backend from frontend/dist)
npm run build
```

Run tests:

```bash
pip install pytest pytest-asyncio
pytest tests -q
```

Notes:

- On first startup the backend creates an `admin` account. Set `ADMIN_PASSWORD`
  beforehand or read the generated bootstrap password from
  `<APP_DATA_DIR>/.admin_bootstrap_password`.
- If the backend hangs at startup in a containerized environment (uvloop
  incompatibility), start uvicorn with `--loop asyncio`.
- The `build` script sets `NODE_OPTIONS=--experimental-global-webcrypto` so the
  PWA/workbox step works on Node 18 as well as Node 20+.

## Docker / Docker Compose

The repo ships a multi-stage [Dockerfile](Dockerfile) and a
[docker-compose.yml](docker-compose.yml). A GitHub Actions workflow
([.github/workflows/docker-build-push.yml](.github/workflows/docker-build-push.yml))
builds the image and pushes it to GHCR automatically:

- on every push to `main` → `ghcr.io/<owner>/tg-signpulse:latest`
- on tags like `v1.2.3` → `ghcr.io/<owner>/tg-signpulse:1.2.3` and `:v1.2.3`
- manually via the `workflow_dispatch` trigger

The image currently targets `linux/amd64`.

Deploy with Docker Compose:

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

On first startup an `admin` account is created. Either set `ADMIN_PASSWORD` in
the environment, or read the generated bootstrap password from
`./data/.admin_bootstrap_password` (the `./data` volume) and change it after
logging in. The container exposes a healthcheck on `/healthz`.

## Recent Optimizations

- Migrated FastAPI startup/shutdown from deprecated `@app.on_event` to a
  `lifespan` context manager; deduplicated `/health` and `/healthz`.
- Replaced the repo-root `jose` / `pyotp` shadow packages with explicit
  `backend.vendor` modules and updated imports; removed the `python-jose` and
  `pyotp` dependencies.
- Replaced unmaintained `passlib` with direct `bcrypt` usage (existing
  `$2b$` hashes remain valid).
- Replaced `print()`-based scheduler logging with structured `logging`.
- Made the frontend static directory configurable (`APP_WEB_DIR`, default
  `frontend/dist`); removed the obsolete Next.js `/_next` mount.
- Aligned ports/config: backend defaults to 8080, Vite dev server to 5173,
  and the CORS/dev-redirect defaults match.
- Frontend toolchain now builds on Node 18: Vite 6.x, `@vitejs/plugin-vue` 5.x,
  `vue-router` 4.x, Tailwind 4.1.x (the previously pinned Vite 8 / Vue Router 5
  require Node 20.19+).
- Added unit tests for password hashing, JWT, TOTP, and settings.
- Added Docker packaging (multi-stage build, `frontend/dist` served by the
  backend) and a GHCR push workflow matching the docker-compose deployment.
- Fixed session-string export from `.session` files: the old `"1"+base64` v1
  format cannot be decoded by kurigram 2.2.x, causing "Invalid base64-encoded
  string" errors when running sign tasks. Exports now use the current format
  (with `api_id`, no version prefix), and stale legacy caches are rebuilt
  automatically.
- Fixed `Failed to preheat chat_id` errors with in-memory sessions: numeric
  chat IDs that need a cached peer/access-hash (private supergroups/channels,
  legacy IDs missing the `-100` prefix) are now resolved by scanning dialogs
  and warming the peer cache before running task actions.

---

## Disclaimer

- This project is intended solely for AI programming technique learning and exchange; it does not encourage or support any form of automation abuse
- The author is not responsible for any consequences arising from the use of this code
- No technical support or deployment services are provided
- Third-party API calls in the code are included only as technical examples; users must comply with the relevant terms of service on their own

---

## Acknowledgements

The Telegram protocol interaction portion of this project references [tg-signer](https://github.com/amchii/tg-signer) by [amchii](https://github.com/amchii).

---

## License

[BSD-3-Clause](LICENSE)
