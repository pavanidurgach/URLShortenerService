ifeq ($(OS),Windows_NT)
PY ?= .venv/Scripts/python.exe
PYTHON ?= py -3
else
PY ?= .venv/bin/python
PYTHON ?= python3
endif

setup:           ## venv with pytest (the kernel itself is stdlib-only and calls no model)
	$(PYTHON) -m venv .venv
	$(PY) -m pip install -q -r requirements.txt

test:            ## kernel, guard, policy, workflow and subagent-file tests (simulated subagents, no LLM)
	$(PY) -m pytest -q

.PHONY: setup test
