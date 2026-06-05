# Pinpoint Trading Scanner — developer shortcuts. All commands use .venv.

PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: help setup test selftest scan scan-targets scan-focus scan-earnings \
        ipo report clean lint

help:
	@echo "make setup         - create .venv (Python 3.10+) and install requirements"
	@echo "make selftest      - offline deterministic pipeline on fixtures (no network)"
	@echo "make test          - run the pytest suite"
	@echo "make lint          - ruff if installed, else a py_compile syntax check"
	@echo "make scan          - live Targets + Focus + Earnings"
	@echo "make scan-targets  - live Targets only"
	@echo "make scan-focus    - live Focus only"
	@echo "make scan-earnings - live Earnings only"
	@echo "make ipo           - recent-IPO initial-high watchlist"
	@echo "make report        - full live scan + HTML report + CSV/XLSX"
	@echo "make app           - launch the Streamlit web app (3 pages)"
	@echo "make clean         - clear output/ and data/snapshots/ (keeps OHLCV cache + IPO highs)"

setup:
	python3 -m venv .venv || python3.12 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

test:
	$(PY) -m pytest -q

selftest:
	$(PY) scan.py --selftest

scan:
	$(PY) scan.py --all

scan-targets:
	$(PY) scan.py --targets

scan-focus:
	$(PY) scan.py --focus

scan-earnings:
	$(PY) scan.py --earnings

ipo:
	$(PY) scan.py --ipo

app:
	.venv/bin/streamlit run app/Home.py

report:
	$(PY) scan.py --report

lint:
	@if $(PY) -c "import ruff" 2>/dev/null; then \
		$(PY) -m ruff check pinpoint scan.py tools tests ; \
	else \
		echo "ruff not installed — running a py_compile syntax check instead" ; \
		$(PY) -m compileall -q pinpoint scan.py tools tests ; \
	fi

clean:
	rm -rf output/* data/snapshots/*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache
	@echo "cleared output/ and data/snapshots/ (OHLCV cache, IPO highs, theme history kept)"
