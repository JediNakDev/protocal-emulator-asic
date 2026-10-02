.DEFAULT_GOAL := help

TOP_MODULE := tt_um_jedinakdev_protocol_emulator
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: help setup test lint check clean

help:
	@echo "make setup - create .venv and install pinned test dependencies (requires uv)"
	@echo "make test  - run cocotb with Icarus Verilog and save test/tb.fst"
	@echo "make lint  - lint RTL with Verilator"
	@echo "make check - run lint and simulation"
	@echo "make clean - remove simulation outputs"

setup:
	uv venv --python 3.11 --python-preference only-managed .venv
	uv pip install --python .venv/bin/python -r test/requirements.txt

test:
	$(MAKE) -C test

lint:
	verilator --lint-only --Wall -Wno-DECLFILENAME --top-module $(TOP_MODULE) -Isrc src/project.v

check: lint test

clean:
	$(MAKE) -C test clean
