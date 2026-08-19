.PHONY: dev up dev-local dev-docker docker-build r-deps r-deps-check dev-frontend dev-backend worker migrate db-up db-down setup check-docker

# Recommended dev path: Docker backend/worker/db, local frontend with HMR.
dev: check-docker
	@echo "Starting Docker OpenCode, backend, worker, Postgres, and Redis..."
	docker compose build backend
	docker compose up -d opencode backend worker
	@echo "Starting frontend at http://localhost:3000..."
	$(MAKE) dev-frontend

# Fast iteration path: start from the existing image without rebuilding.
# Backend code, prompts, and registry are bind-mounted, so code changes are
# live. Rebuild (make dev) only when the Dockerfile, requirements, or asset
# directories (skills/, templates/) change.
up: check-docker
	@echo "Starting Docker OpenCode, backend, and worker from the existing image..."
	docker compose up -d opencode backend worker
	@echo "Starting frontend at http://localhost:3000..."
	$(MAKE) dev-frontend

# Local backend/worker path. Uses the host R library and requires make r-deps.
dev-local: check-docker r-deps-check
	@echo "Starting Postgres and Redis..."
	docker compose up -d postgres redis
	$(MAKE) migrate
	@echo "Starting backend at http://localhost:8000..."
	$(MAKE) dev-backend &
	@echo "Starting Celery worker..."
	$(MAKE) worker &
	@echo "Starting frontend at http://localhost:3000..."
	$(MAKE) dev-frontend

dev-docker: check-docker
	@echo "Starting Docker backend and worker with baked R/Quarto dependencies..."
	docker compose up --build opencode backend worker

docker-build: check-docker
	@echo "Building backend image with standard R packages..."
	docker compose build backend

r-deps:
	@echo "Installing standard R analysis packages into the local R library..."
	cd backend && Rscript --vanilla r-packages.R

r-deps-check:
	@echo "Checking local R analysis packages..."
	cd backend && Rscript --vanilla check-r-packages.R

check-docker:
	@docker info >/dev/null 2>&1 || ( \
		echo "Docker is installed but not accessible from this shell."; \
		echo "If the user is already in the docker group, run: newgrp docker"; \
		echo "One-shot workaround: sg docker -c 'make dev'"; \
		echo "If not in the group: sudo usermod -aG docker $$USER, then log out/in."; \
		exit 1 \
	)

dev-frontend:
	cd frontend && npm run dev

dev-backend:
	cd backend && uvicorn app.main:app --reload --port 8000

worker:
	cd backend && celery -A app.tasks.analysis worker --loglevel=info

migrate:
	cd backend && alembic upgrade head

db-up: check-docker
	docker compose up -d

db-down:
	docker compose down

setup:
	@echo "Setting up OmicsBase..."
	cp -n .env.example .env || true
	cd frontend && npm install
	cd backend && pip install -r requirements.txt
	@echo "Setup done. Use 'make dev' for Docker backend + local frontend, or 'make dev-local' for host R/Python."
