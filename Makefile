.PHONY: help install core install-pip lock lint lint-imports contracts contracts-check strip-template dist typecheck format format-check check ci test test-unit test-integration test-contract test-e2e test-module run clean docker-context docker-build verify verify-docker

PYTHON := python3
VENV := .venv
PRECOMMIT := $(VENV)/bin/pre-commit
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
LINT_IMPORTS := $(VENV)/bin/lint-imports
MYPY := $(VENV)/bin/mypy
PY := $(VENV)/bin/python

help:
	@echo "Доступные команды:"
	@echo "  make install       — установить зависимости через uv sync (рекомендуется)"
	@echo "  make core          — поставить ядро AxisLar из локального wheel"
	@echo "  make install-pip   — установить зависимости через venv+pip (fallback)"
	@echo "  make lock          — обновить uv.lock после изменения зависимостей"
	@echo "  make lint          — ruff check"
	@echo "  make lint-imports  — import-linter"
	@echo "  make contracts     — пересчитать белые списки import-linter из дерева nu/"
	@echo "  make contracts-check — проверить, что белые списки не устарели"
	@echo "  make typecheck     — mypy (strict, nu + tau + eta)"
	@echo "  make format        — ruff format + ruff check --fix"
	@echo "  make format-check  — ruff format --check (без изменений)"
	@echo "  make check         — format-check + lint + lint-imports + contracts-check + typecheck + test"
	@echo "  make ci            — алиас make check"
	@echo "  make test          — pytest"
	@echo "  make test-unit     — только маркер unit"
	@echo "  make test-integration — только маркер integration"
	@echo "  make test-contract — только маркер contract"
	@echo "  make test-e2e      — только маркер e2e"
	@echo "  make test-module M=beta — тесты одного модуля с его покрытием"
	@echo "  make strip-template — снести template-обвязку при форке"
	@echo "  make dist          — собрать дистрибутив с переименованным пакетом"
	@echo "  make docker-context — собрать контекст docker (wheel-ы ядра, продукта, плагинов)"
	@echo "  make docker-build  — собрать образ продукта (docker)"
	@echo "  make verify        — сквозная проверка из wheel: CLI + HTTP"
	@echo "  make verify-docker — то же плюс сборка и запуск образа"
	@echo "  make run           — запуск nu.main"
	@echo "  make clean         — удалить кэши и venv"

# Ядро (axislar-core) не в pyproject: репозиторий ядра пока приватный, а
# uv sync вычищает пакеты, поставленные вручную. Поэтому отдельный таргет.
CORE_WHEEL ?= $(lastword $(sort $(wildcard ../AxisLar/dist/axislar_core-*.whl)))

core:
	@test -n "$(CORE_WHEEL)" || (echo "no wheel in ../AxisLar/dist; run 'make dist' in AxisLar" >&2; exit 1)
	uv pip install "$(CORE_WHEEL)"

install:
	@command -v uv >/dev/null 2>&1 || (echo "uv not found. Install it or use 'make install-pip'."; exit 1)
	uv sync --extra dev
	$(PRECOMMIT) install

install-pip:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"
	$(PRECOMMIT) install

lock:
	@command -v uv >/dev/null 2>&1 || (echo "uv not found."; exit 1)
	uv lock

lint:
	$(RUFF) check nu tau eta plugins

lint-imports:
	$(LINT_IMPORTS)

contracts:
	$(PY) -m eta.sync_contracts

contracts-check:
	$(PY) -m eta.sync_contracts --check

typecheck:
	$(MYPY) nu tau eta plugins

format:
	$(RUFF) format nu tau eta plugins
	$(RUFF) check --fix nu tau eta plugins

format-check:
	$(RUFF) format --check nu tau eta plugins

check: format-check lint lint-imports contracts-check typecheck test

ci: check

test:
	$(PYTEST)

test-unit:
	$(PYTEST) -m unit

test-integration:
	$(PYTEST) -m integration

test-contract:
	$(PYTEST) -m contract

test-e2e:
	$(PYTEST) -m e2e

test-module:
	$(PYTEST) tau/$(M) --cov=nu.$(M)

strip-template:
	$(PY) -m eta.strip_template

# Пакет в разработке называется `nu`; на деплое он переименовывается
# (nu -> DIST_PACKAGE) в копии дерева, чтобы артефакт был уникальным.
DIST_PACKAGE ?= agora_nu
DIST_DIR ?= .deploy

dist: contracts-check
	rm -rf $(DIST_DIR) dist
	$(PY) -m eta.rename_package --to $(DIST_PACKAGE) --from-dir $(DIST_DIR)
	cd $(DIST_DIR) && uv build --out-dir ../dist
	@ls -1 dist

run:
	$(VENV)/bin/python -m nu.main

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(VENV)
	rm -rf .import_linter_cache
	rm -rf .coverage htmlcov .mypy_cache

# Образ не тащит исходники: в контекст кладутся только wheel артефактов.
# Порядок важен — сначала `make dist` в ../AxisLar (ядро), потом здесь.
DOCKER_IMAGE ?= agoralar

docker-context:
	@ls ../AxisLar/dist/axislar_core-*.whl >/dev/null 2>&1 || \
		(echo "no core wheel: run 'make dist' in ../AxisLar" >&2; exit 1)
	rm -rf build/docker
	mkdir -p build/docker/wheels
	cp ../AxisLar/dist/axislar_core-*.whl build/docker/wheels/
	cp dist/agoralar-*.whl build/docker/wheels/
	cp plugins/*/dist/*.whl build/docker/wheels/
	cp Dockerfile .dockerignore build/docker/
	@ls -1 build/docker/wheels

docker-build: docker-context
	docker build -t $(DOCKER_IMAGE):latest build/docker

# Сквозная проверка «как у пользователя»: артефакты → чистый venv → сценарии.
# --docker добавляет сборку и запуск образа; требует docker и соседний AxisLar.
verify:
	bash scripts/verify.sh

verify-docker:
	bash scripts/verify.sh --docker
