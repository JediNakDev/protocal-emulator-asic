.DEFAULT_GOAL := help

TOP_MODULE := tt_um_jedinakdev_protocol_emulator
RTL_SOURCES := $(addprefix src/,project.v pe_host.v pe_global.v pe_imem.v pe_store.v pe_pins.v pe_g2.v pe_cr.v pe_engine.v pe_fifo.v pe_lfsr.v)
export PATH := $(CURDIR)/.venv/bin:$(PATH)
PYTHON ?= $(if $(wildcard $(CURDIR)/.venv/bin/python),$(CURDIR)/.venv/bin/python,python3)

.PHONY: help setup test test-tooling lint synth-check formal check clean model

help:
	@echo "make setup - create .venv and install pinned test dependencies (requires uv)"
	@echo "make test  - run cocotb with Icarus Verilog and save test/tb.fst"
	@echo "make test-tooling - run regressions for the verification commands"
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

test-tooling:
	$(PYTHON) -m pytest -q test/test_tooling.py

lint:
	verilator --lint-only --Wall -Wno-DECLFILENAME --top-module $(TOP_MODULE) -Isrc $(RTL_SOURCES)
	verilator --lint-only --Wall -Wno-DECLFILENAME -DPE_FLOP_STORE --top-module $(TOP_MODULE) -Isrc $(RTL_SOURCES)

synth-check:
	yosys -Q -T -q -p 'read_verilog -lib tools/yosys/ihp_cells.v; read_verilog $(RTL_SOURCES); hierarchy -check -top $(TOP_MODULE); proc; check -assert'
	yosys -Q -T -q -p 'read_verilog -DPE_FLOP_STORE $(RTL_SOURCES); hierarchy -check -top $(TOP_MODULE); proc; check -assert'

# Property module and steps: two-step runs from a free state prove an induction
# step; the reset check runs from power-up.
FORMAL_PROPS := fetch:2 fifo:2 reset:8
# The behavioral model of the instruction store's latch cells
# (sg13cmos5l_dlhrq_1 in synthesis) is a latch by design; any other latch
# yosys infers is still reported. The full log is searched, since some yosys
# versions omit latch inference from quiet output.
STORE_LATCH := Latch inferred for signal .*pe_store\.\\\\g_row\[[0-9]*\]\.g_bit\[[0-9]*\]\.bit_q
FORMAL_SOLVER ?= z3

formal:
	@mkdir -p formal/build
	@set -e; for p in $(FORMAL_PROPS); do \
	  name=$${p%%:*}; steps=$${p##*:}; \
	  yosys -p "read_verilog -formal formal/$$name.v $(RTL_SOURCES); prep -top $${name}_props; flatten; memory -nomap; async2sync; dffunmap; opt_clean; write_smt2 -wires formal/build/$$name.smt2.tmp" > formal/build/$$name.yosys.log 2>&1 || { cat formal/build/$$name.yosys.log; exit 1; }; \
	  mv formal/build/$$name.smt2.tmp formal/build/$$name.smt2; \
	  grep -e "Warning:" -e "Latch inferred" formal/build/$$name.yosys.log | grep -v -e "Replacing memory" -e "$(STORE_LATCH)" || true; \
	  yosys-smtbmc -s $(FORMAL_SOLVER) -t $$steps formal/build/$$name.smt2 > formal/build/$$name.log || { cat formal/build/$$name.log; exit 1; }; \
	  echo "formal: $$name passed"; \
	done

check: lint synth-check formal test-tooling test

model:
	$(MAKE) -C model test

clean:
	$(MAKE) -C test clean
	$(MAKE) -C model clean
	rm -rf formal/build
