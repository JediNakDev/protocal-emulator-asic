.DEFAULT_GOAL := help

TOP_MODULE := tt_um_jedinakdev_protocol_emulator
RTL_SOURCES := $(addprefix src/,project.v pe_host.v pe_global.v pe_imem.v pe_store.v pe_pins.v pe_g2.v pe_engine.v pe_fifo.v pe_lfsr.v)
export PATH := $(CURDIR)/.venv/bin:$(PATH)

.PHONY: help setup test lint synth-check formal check clean model

help:
	@echo "make setup - create .venv and install pinned test dependencies (requires uv)"
	@echo "make test  - run cocotb with Icarus Verilog and save test/tb.fst"
	@echo "make lint  - lint RTL with Verilator"
	@echo "make synth-check - check RTL drivers with Yosys before optimization"
	@echo "make formal - prove the properties in formal/ with yosys-smtbmc (FORMAL_SOLVER, default z3)"
	@echo "make check - run lint, synthesis checks, formal proofs and simulation"
	@echo "make model - build and run the C protocol-engine model tests"
	@echo "make clean - remove simulation outputs"

setup:
	uv venv --python 3.11 --python-preference only-managed .venv
	uv pip install --python .venv/bin/python -r test/requirements.txt

test:
	$(MAKE) -C test

lint:
	verilator --lint-only --Wall -Wno-DECLFILENAME --top-module $(TOP_MODULE) -Isrc $(RTL_SOURCES)
	verilator --lint-only --Wall -Wno-DECLFILENAME -DPE_LATCH_STORE --top-module $(TOP_MODULE) -Isrc $(RTL_SOURCES)

synth-check:
	yosys -Q -T -q -p 'read_verilog $(RTL_SOURCES); hierarchy -check -top $(TOP_MODULE); proc; check -assert'
	yosys -Q -T -q -p 'read_verilog -DPE_LATCH_STORE $(RTL_SOURCES); hierarchy -check -top $(TOP_MODULE); proc; check -assert'

# Property module and steps: two-step runs from a free state prove an induction
# step; the reset check runs from power-up.
FORMAL_PROPS := fetch:2 fifo:2 reset:8
FORMAL_SOLVER ?= z3

formal:
	@mkdir -p formal/build
	@set -e; for p in $(FORMAL_PROPS); do \
	  name=$${p%%:*}; steps=$${p##*:}; \
	  yosys -q -p "read_verilog -formal formal/$$name.v $(RTL_SOURCES); prep -top $${name}_props; flatten; memory -nomap; async2sync; dffunmap; opt_clean; write_smt2 -wires formal/build/$$name.smt2" 2>&1 | grep -v "Replacing memory" || true; \
	  yosys-smtbmc -s $(FORMAL_SOLVER) -t $$steps formal/build/$$name.smt2 > formal/build/$$name.log || { cat formal/build/$$name.log; exit 1; }; \
	  echo "formal: $$name passed"; \
	done

check: lint synth-check formal test

model:
	$(MAKE) -C model test

clean:
	$(MAKE) -C test clean
	$(MAKE) -C model clean
	rm -rf formal/build
