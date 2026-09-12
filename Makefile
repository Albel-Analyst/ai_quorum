SHELL := /bin/bash
PY := uv run python

.PHONY: install setup seed track act reset-db run up down logs restart test lint check env fake

install:          ## local venv
	uv sync

setup:            ## first run: create .env from the example
	@test -f .env || (cp .env.example .env && echo ".env created — fill in SLACK_BOT_TOKEN, SLACK_APP_TOKEN, OPENAI_API_KEY")
	@mkdir -p data records

seed:             ## post a demo thread: make seed CH=C0123 SCENARIO=db MENTION=U0123
	$(PY) -m quorum.tools.seed_demo --channel $(CH) --scenario $(SCENARIO) --mention $(MENTION)

# The tools below run INSIDE the compose container (they share its SQLite). For `make run` on a laptop use `uv run python -m quorum.tools.<tool>` directly.
track:            ## start tracking a thread without clicking: make track CH=C0123 TS=1789202368.848439 BY=U0123
	docker compose exec quorum python -m quorum.tools.track --channel $(CH) --ts $(TS) --by $(BY)

act:              ## drive a card action: make act CH=… TS=… BY=U… ACTION=confirm OPT=A  (open_voting | vote | confirm | record | unpark)
	docker compose exec quorum python -m quorum.tools.act --channel $(CH) --ts $(TS) --by $(BY) $(ACTION) $(OPT)

reset-db:         ## wipe the bot state (tracked threads, decisions journal); Slack messages stay
	docker compose down -v

run:              ## run the agent locally (Socket Mode), uses .env
	$(PY) -m quorum.app

fake:             ## run without an LLM key (deterministic FakeProvider) — handy for UI work
	OPENAI_API_KEY= $(PY) -m quorum.app

up:               ## docker compose: build + start (state in a volume, ADRs in ./records)
	@mkdir -p records
	docker compose up -d --build
	docker compose logs --tail=20 quorum

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
