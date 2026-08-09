.PHONY: backend frontend install test lint build up down pull logs

backend: ## 启动后端（开发模式，标准 asyncio 循环）
	uvicorn backend.main:app --host 127.0.0.1 --port 8080 --reload --loop asyncio

frontend: ## 启动前端开发服务器（http://127.0.0.1:5173）
	cd frontend && npm run dev

install: ## 安装后端 + 前端依赖
	pip install -e .
	cd frontend && npm install

test: ## 运行后端单元测试
	pytest tests -q

lint: ## 运行 ruff 检查
	ruff check backend tests

build: ## 构建前端生产产物
	cd frontend && npm run build

up: ## docker compose 启动
	docker compose up -d

down: ## docker compose 停止
	docker compose down

pull: ## 拉取最新镜像
	docker compose pull

logs: ## 查看容器日志
	docker compose logs -f
