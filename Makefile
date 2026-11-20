PY ?= python3

install:
	$(PY) -m pip install -e ".[dev]"

reset:
	$(PY) -m hayagents.cli reset

test:
	$(PY) -m pytest -q

demo:
	$(PY) -m hayagents.cli demo

replay:
	$(PY) -m hayagents.cli demo --replay
