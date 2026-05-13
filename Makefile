.PHONY: help install dev migrate \
        test test-unit test-integration test-live test-all \
        lint format typecheck \
        image compose-up compose-down compose-stack e2e \
        k8s-validate

PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

help:
	@echo "Targets:"
	@echo ""
	@echo "  install            Create venv and install dev dependencies"
	@echo "  dev                Run uvicorn locally (auto-reload) — http://localhost:8000"
	@echo "  migrate            Run alembic upgrade head"
	@echo ""
	@echo "  test               pytest unit + integration (integration needs Docker)"
	@echo "  test-unit          pytest tests/unit/ only (fast, no Docker)"
	@echo "  test-integration   pytest tests/integration/ (testcontainers Postgres+Timescale)"
	@echo "  test-live          run scripts/live_verify*.sh against the running stack (21+13 cases)"
	@echo "  test-all           test + test-live (full ladder, ~10 min)"
	@echo ""
	@echo "  lint               Run ruff check + format check"
	@echo "  format             Run ruff format (writes)"
	@echo "  typecheck          Run mypy --strict app"
	@echo ""
	@echo "  image              Build the Docker image (deploy/docker/Dockerfile)"
	@echo "  compose-up         docker compose up -d --build --wait"
	@echo "  compose-down       docker compose down -v"
	@echo "  compose-stack      compose-up + gen_pki + init_garage + /etc/hosts (full bootstrap)"
	@echo "  e2e                compose-stack + test-live (spin from scratch and run live)"
	@echo ""
	@echo "  k8s-validate       Render kustomize overlays and validate against k8s schemas"

$(VENV):
	$(PYTHON) -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

dev:
	RM_DEBUG=true $(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

migrate:
	$(PY) -m alembic upgrade head

# -------- tests --------

test:
	$(PY) -m pytest

test-unit:
	$(PY) -m pytest tests/unit/ -q

test-integration:
	$(PY) -m pytest tests/integration/ -q

# Runs both live verification scripts against the running compose stack.
# Assumes `make compose-stack` has been called once.
test-live:
	bash scripts/live_verify.sh
	bash scripts/live_verify_prod_like.sh

test-all: test test-live

# -------- lint / type --------

lint:
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests

format:
	$(VENV)/bin/ruff format app tests

typecheck:
	$(VENV)/bin/mypy app

# -------- docker / compose --------

image:
	docker build -t rainmaker-server:dev -f deploy/docker/Dockerfile .

compose-up:
	docker compose up -d --build --wait

compose-down:
	docker compose down -v --remove-orphans

# Full local bootstrap: stack + dev PKI + Garage layout/key/bucket + /etc/hosts
# entries so curl + fake_node.py can reach NGINX vhosts. Idempotent.
compose-stack: compose-up
	@if [ ! -f var/pki/ca-chain.pem ]; then $(PY) scripts/gen_pki.py; fi
	bash scripts/init_garage.sh
	@if ! grep -q "api.local claim.local node.local" /etc/hosts 2>/dev/null; then \
	  echo "127.0.0.1 api.local claim.local node.local" | sudo tee -a /etc/hosts >/dev/null \
	    && echo "/etc/hosts updated"; \
	fi

e2e: compose-stack test-live

# -------- k8s --------

k8s-validate:
	@for ov in dev staging prod; do \
	  echo "=== overlay $$ov ==="; \
	  kubectl kustomize deploy/k8s/overlays/$$ov > /dev/null && echo "  kustomize: OK"; \
	  if command -v kubeconform >/dev/null 2>&1; then \
	    kubectl kustomize deploy/k8s/overlays/$$ov | \
	      kubeconform -summary -strict -kubernetes-version 1.30.0 \
	        -skip Ingress -ignore-missing-schemas; \
	  else \
	    echo "  kubeconform not installed — install from https://github.com/yannh/kubeconform for deep schema validation"; \
	  fi; \
	done
