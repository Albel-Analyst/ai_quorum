SHELL := /bin/bash
PY := uv run python

.PHONY: install run up down logs restart test lint check env fake

install:          ## local venv
	uv sync

run:              ## run the agent locally (Socket Mode), uses .env
	$(PY) -m quorum.app

fake:             ## run without an LLM key (deterministic FakeProvider) — handy for UI work
	OPENAI_API_KEY= $(PY) -m quorum.app

up:               ## docker compose: build + start
	docker compose up -d --build
	docker compose logs -f --tail=50 quorum

down:
	docker compose down

logs:
	docker compose logs -f --tail=200 quorum

restart:
	docker compose restart quorum

test:
	uv run pytest -q

lint:
	uv run ruff check quorum tests

check: lint test  ## what CI would run

env:              ## verify every credential in .env (Slack, OpenAI, Exa, Confluence, Jira)
	$(PY) -m quorum.tools.check_access
