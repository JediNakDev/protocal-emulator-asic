# Verification

How this chip was verified, what each check proves, and what is still unproven.
It assumes no knowledge of the repository.
[spec.md](spec.md) defines the chip, [protocols.md](protocols.md) lists the programs and their limits, and [area-log.md](area-log.md) records the physical builds.

## Summary

The chip has two programmable engines behind a Host port, 512 instruction words in latches, and shared blocks for CRC, bit state machines, edge capture and clock recovery ([spec.md](spec.md)).

**Verified, in RTL simulation, through the chip's pins:** UART, SPI, I2C, JTAG, SWD, PS/2, CAN, low-speed USB host, 10BASE-T and waveform capture and replay.
Each one runs its program on the chip against a separate peer that checks the wire in absolute time.
This run passed 131 of 131 tests with the default latch store, and 131 of 131 with the flip-flop store.

**Also verified:** random programs match a Python model written from the specification, cycle by cycle at every pad; three small formal properties pass; three protocols chosen after the RTL was finished pass with no RTL change.

**Verified on an older netlist, not on the current RTL:** 18 tests passed at gate level on the routed netlist of build 8, without timing ([area-log.md](area-log.md)).
Gate-level simulation was not run for this report, because the netlist and the PDK are not on this machine.

**Not verified:** nothing has run on a board, an FPGA or silicon.
Timing closure of the current RTL is open: build 10 has no recorded result, and builds 8 and 9 miss setup at the slow corner.
Simulation does not establish timing closure.
Gate-level simulation here is functional and has no delay annotation.
See [known gaps](#known-gaps).

## This run

All results below are from one fresh run on 2026-10-31, unless a line says otherwise.

| Item | Value |
| --- | --- |
| Commit | `620b794128e607f8a0023df9dba6fe129f91e655` |
| Working tree | `src/` unchanged; `test/test_heldout.py`, `test/test_concurrency.py`, their docs and three `programs/*.pasm` files were present and uncommitted |
| RTL hash, area-log method | `9eb0d482358186b6f55a8295cebaa6fc3bf7163d7d026950efed817ea3d765de`, build 10 in [area-log.md](area-log.md) |
| Simulator | Icarus Verilog 13.0 (stable) (v13_0) |
| Other tools | cocotb 2.0.1, Python 3.11.15, Verilator 5.052, Yosys 0.70+post, Z3 5.3.3, pytest 8.4.2 |
| Host | macOS arm64, Darwin 27.0.0 |

| Stage | Command | Result | Seconds |
| --- | --- | --- | --- |
| Lint | `make lint` | Exit 0, both storage variants | 3 |
| Synthesis check | `make synth-check` | Exit 0, both storage variants | 3 |
| Formal | `make formal` | Exit 0; `fetch`, `fifo` and `reset` pass | 10 |
| Tooling tests | `make test-tooling` | 6 passed | 0.30 |
| Full RTL suite, latch store | `make test SIM_BUILD=sim_build/job3 COCOTB_RESULTS_FILE=sim_build/job3/results.xml` | `TESTS=131 PASS=131 FAIL=0 SKIP=0` | 1059 |
| Held-out protocols | `make test COCOTB_TEST_MODULES=test_heldout SIM_BUILD=sim_build/job3b COCOTB_RESULTS_FILE=sim_build/job3b/results.xml` | `TESTS=12 PASS=12 FAIL=0 SKIP=0` | 179 |
| Full RTL suite, flip-flop store | `make test FLOP=yes SIM_BUILD=sim_build/job3_flop COCOTB_RESULTS_FILE=sim_build/job3_flop/results.xml` | `TESTS=131 PASS=131 FAIL=0 SKIP=0` | 1463, sharing the machine with other jobs |
| `test_protocols` alone | `make test COCOTB_TEST_MODULES=test_protocols SIM_BUILD=sim_build/job3c COCOTB_RESULTS_FILE=sim_build/job3c/results.xml` | `TESTS=15 PASS=14 FAIL=1`, see [known gaps](#known-gaps) | 52 |
| Extra random soak | `make test COCOTB_TEST_MODULES=test_random PE_RANDOM_SEEDS=200 PE_RANDOM_SEED=1000 SIM_BUILD=sim_build/job3d COCOTB_RESULTS_FILE=sim_build/job3d/results.xml` | `TESTS=1 PASS=1`, 200 seeds match; seed 1045 compared pads only | |
| Gate level | `make test GATES=yes` | **Not run**: no `test/gate_level_netlist.v`, no PDK, and no GitHub release to take a netlist from | |

The full suite by module:

| Module | Tests | Module | Tests |
| --- | --- | --- | --- |
| `test_can` | 26 | `test_eth` | 11 |
| `test_engine` | 20 | `test_usb` | 9 |
| `test_host` | 16 | `test_waveform` | 8 |
| `test_protocols` | 15 | `test_ps2` | 7 |
| `test_features` | 11 | `test_swd` | 5 |
| `test_jtag` | 2 | `test_random` | 1 |

The latch full-suite run reports "60 random seeds match the model", with 0 seeds reduced to pads only.

For comparison, GitHub Actions run 40358866847 at the same commit passed on Icarus 12.0: `TESTS=131 PASS=131 FAIL=0` and a 200-seed soak, `TESTS=1 PASS=1`.
That result comes from `gh run view`, not from this run.
`.github/workflows/test.yaml` runs `make lint synth-check test-tooling test`, the soak, and `make formal FORMAL_SOLVER=yices` on every push.

## Layers

### Top-level cocotb tests through the chip's pins

`test/chip.py` drives the chip through `tb.v` as a Host would: it loads programs, starts the engines and moves data over the Host port.
Tests act on the chip's pins, and also read `contention` from the board model.

**Proves:** the RTL loads and runs each program, and data arrives correct at both ends, including error paths such as `test_swd_wait`, `test_can_node_bus_off` and `test_usb_keep_alive`.
`test/tb.v` models the board and reports `contention` on shared lines, which `test_protocols`, `test_swd`, `test_usb` and `test_heldout` check.

**Cannot prove:** anything about real pads, or about traffic the tests do not send.
A pass covers these programs, rates and scenarios, not every use.

**Run:** `make test`.
Use `FLOP=yes` for the flip-flop store; latches are the default.
`COCOTB_TEST_MODULES=` and `COCOTB_TEST_FILTER=` select tests.

### Independent protocol peers

Each protocol has a peer written against the protocol, not against the program.
The shared peers are in `test/peers.py`: `uart_decode`, `uart_receive`, `uart_transmit`, `SpiPeripheral` and `I2CTarget`.
The others are in their test modules: `TapModel`, `DebugPortModel`, `HostModel`, `DeviceModel`, `CanBus`, `CanNode`, `UsbDevice`, `TxMonitor` and `drive_rx`.

**Proves:** the chip's wire behavior satisfies a second implementation of the protocol, with its own CRCs and state machines.

**Cannot prove:** interoperation with real parts.
A peer and a program can share one misreading of a standard, since both were written here.
The USB and SWD peers import PID and ACK constants from `pe.proto`, so those constants are not independent; their CRCs are peer-owned.

**Run:** with the top-level tests.

### Absolute-time wire checks

Peers measure the wire in nanoseconds from simulation time, not in chip cycles (`test/peers.py`, commit 222a62b).
`Wave` records edges, `clock_violations` checks clock periods with a 1% tolerance, and `setup_hold_violations` checks data against clock edges.
Individual tests add the protocol's own limits, for example I2C in `test/test_protocols.py`.

**Proves:** a program's timing meets the figures the peers check, at the stated clock.
A check written in cycles would scale with the clock and could not show this.

**Cannot prove:** timing at a real pad.
The simulated wire has no delay, capacitance or rise time, and the clock has no jitter or duty-cycle error.

**Run:** with the top-level tests.

### Wrong-clock controls

`fails_at_wrong_clock` in `test/chip.py` reruns a session at a clock the program was not built for, using `WRONG_HZ` or a value the test gives, and requires it to fail.
The suite has 13 of these in the default modules and 3 in `test_heldout`.
Each asserts which check fired, so a failure for an unrelated reason does not count.
Most expect a timing message; some receive-side controls expect wrong or missing data instead, as listed under [negative controls](#negative-controls).

**Proves:** the checks are live: a program at the wrong rate is rejected.

**Cannot prove:** that the checks are tight enough for every limit.
Each control exercises one wrong clock, and the first failed check ends it.

**Run:** with the top-level tests; `COCOTB_TEST_FILTER=wrong_clock` selects them.

### Random cycle-by-cycle comparison with the model

`tools/pe/model.py` is a 786-line Python model written from [spec.md](spec.md), sharing no code with the RTL.
`test/test_random.py` runs random programs and configurations on both for 740 cycles per seed.
It compares every pad on every cycle in both clock phases, then all state the Host can read.
The default is 60 seeds, and 4 at gate level; `PE_RANDOM_SEEDS` and `PE_RANDOM_SEED` change that.

**Proves:** on the seeds run, the RTL and a separate reading of the specification agree on engine behavior for random programs, not only the written protocol programs.

**Cannot prove:** correctness beyond the seeds, or correctness of the specification.
The Host port is not modelled: its write latency and the counter start are calibrated from the chip (36 cycles in this run).
A seed whose model state is still changing at the end compares pads only.
Control R3 below shows the limit: 60 seeds missed one injected bug and a 200-seed run found it.

**Run:** `make test COCOTB_TEST_MODULES=test_random`, and add `PE_RANDOM_SEEDS=200 PE_RANDOM_SEED=1000` for a soak.

### Formal checks

`make formal` runs three yosys-smtbmc properties (`FORMAL_PROPS := fetch:2 fifo:2 reset:8` in `Makefile`).

| Property | States | Depth and type | Scope |
| --- | --- | --- | --- |
| `formal/fetch.v` | `ir` holds the memory word at PC, and `cur` equals `ir` when the execute slot is empty | Induction step, 2 | Engine fetch |
| `formal/fifo.v` | Queue levels never exceed the mode's entries | Induction step, 2 | `pe_fifo` only |
| `formal/reset.v` | All outputs are 0 while `rst_n` is low and for two cycles after | Bounded, 8 steps from any power-up state | Whole chip |

**Proves:** these three statements, at the scopes above.

**Cannot prove:** anything else about the design.
The induction runs do not check the base case.
`formal/fetch.v` replaces the store with a behavioural memory, so the latch store is outside that proof.
The reset property is bounded, and is a statement about reset only.

**Run:** `make formal`.

### Gate-level simulation

`make test GATES=yes` runs tests on `test/gate_level_netlist.v` with `-DFUNCTIONAL` and the PDK cell models; it needs `PDK_ROOT`.

**Proves:** the netlist has the same function as the RTL on the tests run, and that the flow kept the connections those tests use.
This is the check that catches the disconnected row gates listed under [bugs](#real-bugs-the-verification-caught).

**Cannot prove:** timing.
There is no delay annotation, so this simulation says nothing about setup, hold or the latch store's half-cycle path.

**Latest recorded result:** build 8's routed netlist, without timing, passed 18 named tests ([area-log.md](area-log.md)): `test_every_instruction_word`, `test_reset_state`, `test_page_jumps`, `test_instruction_pages`, `test_uart_tx_fast`, `test_uart_rx`, `test_spi_controller_mode0`, `test_i2c_controller_400khz`, `test_jtag_idcode_ir_dr_bypass`, `test_swd_read_write`, `test_ps2_chip_as_keyboard`, `test_ps2_chip_as_host`, `test_can_receive`, `test_can_node_transmit_error_counts`, `test_can_node_bus_integration`, `test_usb_get_descriptor`, `test_eth_receive` and `test_eth_transmit`.
Build 8 has RTL hash `87301e6a...a9e1`, which is not the current RTL.
Commit 490de6e says a Yosys equivalence check proved the engine unchanged by the later `out_raw` change, but no script for that check is in the repository.
No random, waveform or held-out test is recorded at gate level for build 8.

**Not run here:** see [this run](#this-run).
`.github/workflows/gds.yaml` runs only on a published release, `gh release list` shows none, and the last successful run of that workflow was at 17995b3, on older RTL.

**Run:** `make test GATES=yes` with a netlist in place (`test/README.md`); in CI, the `gl_test` job of `.github/workflows/gds.yaml`.

## Negative controls

### Controls in the suite

These passed in this run, which means each check rejected what it should.

| Control | Required outcome |
| --- | --- |
| `test_uart_wrong_clock` | TX: a `timing:` message. RX: wrong bytes, framing or lost words |
| `test_spi_wrong_clock`, `test_jtag_wrong_clock`, `test_swd_wrong_clock` | `timing: clock period` |
| `test_i2c_wrong_clock` | `timing: SCL period` |
| `test_ps2_device_wrong_clock` | `timing: CLK`, at 30 MHz |
| `test_ps2_host_wrong_clock` | `timing: inhibit`, at 60 MHz |
| `test_can_node_wrong_clock` | `timing: CAN edge` |
| `test_can_receive_wrong_clock` | Wrong received frames |
| `test_usb_wrong_clock` | `timing: USB bit rate`, 48 MHz program at 50 MHz |
| `test_eth_wrong_clock` | `timing: Manchester` |
| `test_eth_receive_wrong_clock` | Unparsable or wrong records |
| `test_capture_wrong_clock` | Wrong captured intervals |
| `test_ws2812_wrong_clock` | `timing: DIN low 1050 ns` |
| `test_i2s_wrong_clock` | `timing: clock period` |
| `test_onewire_wrong_clock` | `no presence` at 40 MHz, a `timing:` message at 62.5 MHz |
| `test_can_node_bad_crc_not_acked` | A frame with a wrong CRC is not acknowledged |
| `test_usb_reply_after_deadline` | A reply starting at 17.25 bit times is treated as missing |
| `test_host_duplex_throughput` | The 64-cycle overload loses words or starves |
| `test_host_receive_throughput` | A word every 42 cycles loses words |
| `test_capture_backpressure_reports_loss` | A full receive queue does not hide a lost edge |
| `test_swd_bad_request_and_recovery` | A request with bad parity gets no ACK, and a line reset recovers the target |
| `test_i2s_left_justified_rejected` | A program with LRCLK one clock late fails the peer's word boundaries |
| `test_onewire_bad_crc` | One flipped ROM bit gives a `CRC8` failure |
| `test_formal_stops_on_synthesis_failure`, `test_formal_reports_inferred_latches` | `make test-tooling`: `make formal` fails on these |

### Controls run for this report

Each one changed a copy of `src/`, never the tree itself.
These are 10 hand-picked mutants, and they show that the checks can fail; they are not a coverage measure.
The repository has no mutation tooling.

Simulation mutants use `make test SRC_DIR=<copy>/src COCOTB_TEST_MODULES=<module> SIM_BUILD=<dir> COCOTB_RESULTS_FILE=<dir>/results.xml`.
Formal mutants copy `src/` and `formal/` to a folder and run `make -f <repo>/Makefile formal FORMAL_PROPS=<prop>` there.

| ID | Change | Check | Result |
| --- | --- | --- | --- |
| F1 | `pe_engine.v`: remove the Host write snoop on fetch, so `fetch_word` is `imem_data` and `hold_word` is `ir` | `fetch` | Fails at `formal/fetch.v:111` |
| F2 | `pe_fifo.v`: `assign tx_full = 1'b0;` | `fifo` | Fails at `formal/fifo.v:97` |
| F3 | `pe_pins.v`: `cfg[i] <= 8'h0C` on reset | `reset` | Fails at `formal/reset.v:41` |
| R1 | `pe_lfsr.v`: invert the feedback choice, `sh \| (par ? 32'd0 : ins)` | `test_random` | Fails, seed 2, pads differ at cycle 318 |
| R2 | `pe_g2.v`: `emit_v <= step & entry[6];` ignores the enable | `test_random` | Fails, seed 15, final state differs |
| R3 | `pe_cr.v`: tie-break `<=` becomes `<` in the edge choice | `test_random`, `test_eth` | **Survives seeds 1-60 and `test_eth` (11 of 11).** Fails at seed 1000 of a 200-seed run, pads differ at cycle 346 |
| R4 | `pe_pins.v`: invert the open-drain output level | `test_random` | Fails, seed 3, cycle 0 |
| R5 | `pe_engine.v`: timed wait does not count, `if (1'b0) n_x = x - 16'd1;` | `test_random` | Fails, seed 11, cycle 214 |
| R6 | `pe_engine.v`: restore the bug fixed in 0622045, `cap_prev <= cap_in;` | `test_random`, `test_features` | Fails: seed 11 final state, and `test_capture_pin_change_is_not_an_edge`; `TESTS=12 PASS=10 FAIL=2` |
| S1 | `pe_store.v`: cut the row select of rows 193, 295 and 397 | `test_every_instruction_word` | Fails with exactly words 193, 295 and 397, each reading `0x0` |

Clean RTL passes the same 200 seeds from 1000 ([this run](#this-run)), so the R3 failure is the mutant's.
R3 was run against `test_random` and `test_eth` only, not the other modules.

`make lint` and `make synth-check` had no fresh failure control.
The historic evidence for `synth-check` is commit a596be4.

Commit 0622045 records "12 of 12 injected RTL bugs", and `formal/README.md` also describes mutation results.
Those mutants are not recorded in the repository, so those figures are historic claims that this run did not reproduce.

## Real bugs the verification caught

The "Check" column names what found the bug where the source says so, and otherwise the regression that now guards it.

| Bug | Check | Fix | Source |
| --- | --- | --- | --- |
| Clock-tree synthesis on build 7 disconnected inputs of the gates of instruction rows 193, 295 and 397 | The flow's disconnected-pin check stopped the build; at gate level `test_every_instruction_word` fails at exactly those words | One clock-gating cell per row | [area-log.md](area-log.md), 490de6e, `test/test_engine.py:569` |
| A `CAPCFG` write could record a false capture, set the flag and raise an overrun | Random comparison with the model | Load the previous level from the new pin; `test_capture_pin_change_is_not_an_edge` | 0622045 |
| After a stuff error following a good CRC, the CAN node acknowledged into the next frame | `test_can.py`, changed in the same commit | End the frame on an error in the delimiter wait | 8192ceb |
| FIFO read races, and all-ones CAN payloads colliding with the frame separator | Regressions at the top-level pins | Hold the high byte and status at low-byte fetch; 15-bit words with a reserved top bit | 7cfd3b9 |
| A shared loop iterator produced conflicting drivers in the pin block | The GDS flow | Scope each iterator; add `make synth-check` | a596be4 |
| The gate-level testbench failed to elaborate and produced no `results.xml` | Running it | Load the PDK's UDP primitives | cf84f12 |
| SWD sent the data phase after WAIT or FAULT, so write data could read as a request | Design review; `test_swd_wait`, `test_swd_fault_and_abort` | Skip the data phase after any ACK other than OK | B5, [design-review.md](design-review.md) |
| A CAN transmit queue running dry stalled the node with TXD at the last bit, which can hold the bus dominant | Design review; `test_can_node_transmit_underrun` | Release TXD and signal the error | B14, [design-review.md](design-review.md) |
| The first Ethernet receiver passed at +/- 9 ns and failed at +/- 10 ns, short of the 802.3 limit of +/- 13.5 ns | Jitter tests at the pins | Run at 50 MHz; G7 takes the nearest transition | B12, [design-review.md](design-review.md) |
| The PS/2 device's acknowledge clock broke the 50 µs high limit | Absolute-time peers, added in the same commit | Program change | 222a62b |
| Capture could lose timestamp words without reporting it | `test_capture_backpressure_reports_loss` | Nonblocking pushes; dropped words set receive overflow | 194ee03 |
| `make formal` ignored the exit status of its Yosys step | `test_formal_stops_on_synthesis_failure` | Stop on a Yosys failure | 17c062f |
| The latch check missed inferred latches on Yosys 0.33, whose quiet output omits them | `test_formal_reports_inferred_latches` in CI | Search the full log | 8820e45 |
| Yosys `share` merged shifters and put about 9 ns ahead of the `jmp_base` adder | Physical timing reports | Build `out_raw` and its mask from multiplexers | Build 9 discussion, [area-log.md](area-log.md) |

## Traceability

Counts are tests in this run.
Default modules passed on the latch and flip-flop stores; the held-out rows ran on the latch store only.
"RTL" means this run.
"GL build 8" means the older recorded result, on functional gate-level simulation without timing, and was not rerun.

| Protocol | Role | Tests | Peer | Rate | Evidence |
| --- | --- | --- | --- | --- | --- |
| UART | TX and RX | 4: `test_uart_tx`, `_tx_fast`, `_rx`, `_wrong_clock` | `uart_decode`, `uart_receive`, `uart_transmit` | 115,741 baud (115,200 target, CLKDIV 53); 3.125 Mbaud | RTL; GL build 8: `test_uart_tx_fast`, `test_uart_rx` |
| SPI | Controller, modes 0-3 | 7: `test_spi_controller_mode0` to `mode3`, `_12mhz_bypass`, `test_spi_host_pause`, `_wrong_clock` | `SpiPeripheral` | 6.25 MHz; 12.5 MHz with bypass | RTL; GL build 8: mode 0 |
| I2C | Controller | 4: `test_i2c_controller_100khz`, `_400khz`, `_400khz_no_stretch`, `test_i2c_wrong_clock` | `I2CTarget` | 100 and 400 kHz | RTL; GL build 8: 400 kHz |
| JTAG | Controller | 2: `test_jtag_idcode_ir_dr_bypass`, `test_jtag_wrong_clock` | `TapModel` | 6.25 MHz | RTL; GL build 8: IDCODE |
| SWD | Host | 5: all in `test_swd.py` | `DebugPortModel` | 6.25 MHz | RTL; GL build 8: `test_swd_read_write` |
| PS/2 | Device and host | 7: all in `test_ps2.py` | `HostModel`, `DeviceModel` | 10-16.7 kHz clock ([design-review.md](design-review.md)) | RTL; GL build 8: `test_ps2_chip_as_keyboard`, `test_ps2_chip_as_host` |
| CAN | Listen-only | 5: `test_can_receive*` | `CanBus`, `CanNode` | 1 Mbit/s, clock offset +/- 1.5% | RTL; GL build 8: `test_can_receive` |
| CAN | Node | 21: `test_can_node_*` | `CanBus`, `CanNode` | 1 Mbit/s, clock offset +/- 1.5% | RTL; GL build 8: `test_can_node_transmit_error_counts`, `test_can_node_bus_integration` |
| USB | Low-speed host | 9: all in `test_usb.py` | `UsbDevice` | 1.5 Mbit/s at 48 MHz | RTL; GL build 8: `test_usb_get_descriptor` |
| 10BASE-T | Transmitter | 3: `test_eth_transmit`, `test_eth_wrong_clock`, `test_eth_link_pulses` | `TxMonitor` | 10 Mbit/s at 50 MHz | RTL; GL build 8: `test_eth_transmit` |
| 10BASE-T | Receiver | 8: `test_eth_receive*` | `drive_rx` | 10 Mbit/s at 50 MHz, jitter up to +/- 13.5 ns | RTL; GL build 8: `test_eth_receive` |
| Waveform | Capture and replay | 8: all in `test_waveform.py` | `Wave`, `uart_decode` | One-cycle resolution | RTL only |
| WS2812 | Transmitter, held out | 2 in `test_heldout.py` | `ws2812_decode` | 793.65 kHz | RTL only |
| 1-Wire | Controller, held out | 6 in `test_heldout.py` | `OneWireDevice` | Standard speed | RTL only |
| I2S | Controller transmitter, held out | 4 in `test_heldout.py` | `i2s_receive` | 47,348.5 Hz | RTL only |

`test_eth_receive_long_frame` and `test_eth_link_pulses` are skipped at gate level (`skip=GATES` in `test/test_eth.py`).

## Held-out protocols

[heldout-protocols.md](heldout-protocols.md) records three protocols chosen after the RTL was finished: WS2812 (10 words), 1-Wire (19 words) and I2S (12 words).
Together they use 41 of 512 instruction words on engine 0, with no change to `src/`.
Each has a peer in `test/test_heldout.py` and at least one negative control.

This run: `TESTS=12 PASS=12 FAIL=0 SKIP=0`.

This is evidence of flexibility: the chip took protocols it was not designed for, using only programs.
It is RTL simulation only, and `test_heldout` is not in the default module list of `test/Makefile`, so `make test` and CI do not run it.

## Concurrency

Which pairs of programs run together is recorded in [concurrency.md](concurrency.md); its suite was not run for this report.
It reports 13 single-engine programs and 78 pairs at the 50 MHz profile: 73 measured-pass, 1 passing only at reduced traffic, 1 measured-fail, 1 blocked by G2, 1 blocked by hard-coded pins and 1 not measured.
The measured failure is Ethernet TX with Ethernet RX, limited by Host port bandwidth.
That document records `test_concurrency` at 95 of 95 cases in each engine order, on Icarus Verilog 13.0 at the same base commit; a passing `test_limit_*` case there means the documented failure still occurs.
They are RTL simulation only.

## Known gaps

- **No physical evidence.** Nothing has run on a board, an FPGA or silicon.
- **Timing closure is open.** Build 10, the current RTL, has no recorded result in [area-log.md](area-log.md). Build 8 misses slow-corner setup by 2.005 ns, and build 9 by 2.116 ns with 0.026 ns of fast-corner hold violation, both at 50 MHz. Build 3 met every corner, but on an older four-page flip-flop design. The Tiny Tapeout sign-off filter is `*typ*`, so a flow "success" does not cover the slow corner.
- **No current physical build.** The current RTL has no recorded GDS, precheck or gate-level result.
- **Gate level is a subset on older RTL.** See [gate-level simulation](#gate-level-simulation). It has no delay annotation.
- **Latch store timing.** The half-cycle write path is not exercised by any simulation here.
- **Formal scope.** Three properties, with the limits listed above. The latch store is outside the fetch proof.
- **The model excludes the Host port,** and default seeds can miss a bug: R3 survived 60 seeds.
- **`test_i2c_controller_400khz` fails when `test_protocols` runs alone on Icarus 13.0.** The message is `timing: SCL period 2499.9999999990687 ns at 400000 Hz`. The bound at `test/test_protocols.py:237` is exact, with no tolerance, and the shortfall of about 2^-30 ns fits double-precision rounding of nanosecond timestamps. The same test passed in the full suite on the same Icarus 13.0, so it depends on when the test runs in simulation time. The cause beyond that was not established. This is a fragile check: the shortfall is far smaller than the 20 ns clock period. The test also passed in CI on Icarus 12.0.
- **Suites outside `make test` and CI.** `test_heldout` and `test_concurrency` are not default modules.
- **Lint and synthesis check** had no fresh failure control.
- **Mutation history.** The 12-of-12 figure in 0622045 and the claim in `formal/README.md` have no recorded mutants or script.
- **Peers are independent in code only,** and have not met real parts.
- **Not modelled at all:** pad timing, metastability, clock duty cycle, analog front ends.
- **Roles not built:** SPI, I2C, JTAG and SWD are controller only, 10BASE-T has no collision handling or link state ([protocols.md](protocols.md#limits-found-during-verification)), and the USB program is a host only (`usb_ls_host`).
- **Owner confirmation.** [design-review.md](design-review.md) records latch storage and 512 words as awaiting the owner's confirmation.
- **`make model` was not run.** It belongs to an older C model with a different instruction set.
- **`make test LATCH=yes`** appears in `README.md` and `test/README.md`, but `test/Makefile` has no such variable and ignores it. Latches are the default, and `FLOP=yes` selects the other store.

## AI use

TODO: project owner to complete.
