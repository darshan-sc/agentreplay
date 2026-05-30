PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
PY := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),$(PYTHON))
GO ?= go
GOCACHE ?= $(CURDIR)/.cache/go-build
GOMODCACHE ?= $(CURDIR)/.cache/go-mod
GOENV := GOCACHE=$(GOCACHE) GOMODCACHE=$(GOMODCACHE)

.PHONY: setup setup-all build-cli test test-go test-python test-pytest validate-sample inspect-sample generate-sample-tests smoke-record smoke-replay langgraph-record langgraph-replay clean

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install -e "python[dev]"

setup-all:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip setuptools wheel
	$(VENV_PYTHON) -m pip install -e "python[dev,langgraph]"

build-cli:
	mkdir -p bin
	$(GOENV) $(GO) build -o bin/agentreplay ./cmd/agentreplay

test: test-go test-python

test-go:
	$(GOENV) $(GO) test ./...

test-python:
	$(GOENV) $(PY) -m unittest discover -s python/tests

test-pytest:
	$(GOENV) $(PY) -m pytest

validate-sample:
	$(GOENV) $(GO) run ./cmd/agentreplay validate traces/sample.replay.jsonl

inspect-sample:
	$(GOENV) $(GO) run ./cmd/agentreplay inspect traces/sample.replay.jsonl

generate-sample-tests:
	$(GOENV) $(GO) run ./cmd/agentreplay generate-tests traces/sample.replay.jsonl --framework pytest --out tmp/test_agent_replays.py

smoke-record:
	$(GOENV) $(GO) run ./cmd/agentreplay record --out tmp/openai-smoke.replay.jsonl -- $(PY) python/examples/openai_record_smoke.py

smoke-replay:
	$(GOENV) $(GO) run ./cmd/agentreplay replay tmp/openai-smoke.replay.jsonl -- $(PY) python/examples/openai_record_smoke.py

langgraph-record:
	$(GOENV) $(GO) run ./cmd/agentreplay record --out tmp/langgraph-demo.replay.jsonl -- $(PY) python/examples/langgraph_demo.py

langgraph-replay:
	$(GOENV) $(GO) run ./cmd/agentreplay replay tmp/langgraph-demo.replay.jsonl -- $(PY) python/examples/langgraph_demo.py

clean:
	rm -rf .cache .pytest_cache python/.pytest_cache python/agentreplay.egg-info
