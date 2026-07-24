.PHONY: install test lint run

VENV = venv
PYTHON = $(VENV)/Scripts/python.exe
PIP = $(VENV)/Scripts/pip.exe
PYTEST = $(PYTHON) -m pytest
CLIENT = client

install:
	$(PIP) install -r requirements.txt
	cd $(CLIENT) && npm install

test:
	set AUTOPILOT_DISABLE_LLM=1 && $(PYTEST) tests/ -v --tb=short

test-unit:
	set AUTOPILOT_DISABLE_LLM=1 && $(PYTEST) tests/test_runtime.py tests/test_mcp.py -v --tb=short

test-integration:
	set AUTOPILOT_RUN_INTEGRATION=1 && $(PYTEST) tests/test_integration.py -v --tb=short

lint:
	$(PYTHON) -m ruff check src/ tests/
	$(PYTHON) -m mypy src/ tests/

run:
	start /b $(PYTHON) run_server.py
	cd $(CLIENT) && npm run dev
