.PHONY: help install dev migrate test lint typecheck image compose-up compose-down e2e k8s-validate

PYTHON ?= python3
VENV ?= .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

help:
	@echo "Targets:"
	@echo "  install        Create venv and install dev dependencies"
	@echo "  dev            Run uvicorn locally (auto-reload)"
	@echo "  migrate        Run alembic upgrade head"
	@echo "  test           Run pytest (unit + integration if Docker is up)"
	@echo "  lint           Run ruff check + format check"
	@echo "  typecheck      Run mypy"
	@echo "  image          Build the Docker image"
	@echo "  compose-up     Bring up the local stack (postgres+garage+vernemq+app)"
	@echo "  compose-down   Tear down the local stack"
	@echo "  e2e            Run end-to-end scenarios against compose stack"
	@echo "  k8s-validate   Render kustomize overlays and validate against k8s schemas"

$(VENV):
	$(PYTHON) -m venv $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

dev:
	RM_DEBUG=true $(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

migrate:
	$(PY) -m alembic upgrade head

test:
	$(PY) -m pytest

lint:
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests

format:
	$(VENV)/bin/ruff format app tests

typecheck:
	$(VENV)/bin/mypy app

image:
	docker build -t rainmaker-server:dev -f deploy/docker/Dockerfile .

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down -v

e2e: compose-up
	$(PY) scripts/fake_node.py --base-url http://localhost:8000

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
