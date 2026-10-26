# Area and timing log

Each entry records the source state, how the numbers were produced, and what they mean.
Numbers from yosys are cell area before placement; routing, clock tree and fill are not included.

## 2026-10-26: timing-driven builds, six-page flip-flops and eight-page latches

Source: working branch `issue-14-remaining`, on `32b00c202aff6a85a6b1b14bcfbfc938e50ea11f`, local LibreLane 3.1.0.dev3 builds with the CMOS5L 8x4 template as in the entry below.
An RTL hash here is the SHA-256 of `json.dumps({"src/<file>": sha256, ...}, sort_keys=True)` over the build's `src/*.v`.
All builds add timing-driven placement and timing repair after global routing (`PL_TIMING_DRIVEN`, `RUN_POST_GRT_RESIZER_TIMING`, `RUN_POST_GRT_DESIGN_REPAIR` in `src/config.json`).

| Build | RTL | Synthesized area | Placement utilization | Result |
| --- | --- | --- | --- | --- |
| 3 | Four pages of flip-flops, G7 with nearest-edge selection (RTL hash `beabf031...7e9c`) | 694,245 µm² | 61.1% | Complete: setup and hold met at every corner, DRC, LVS and antenna clean |
| 4 | Six pages of flip-flops | 897,796 µm² | 78.7% | Failed: detailed placement of the clock tree (DPL-0036) |
| 5 | Six pages, store reset simplified (6,913 tie cells to 1) | 845,749 µm² | 74.8% | Failed: DPL-0036 again |
| 6 | Eight pages of latches (RTL hash `55dcbd7c...63e4`) | 698,232 µm² | 63.7%, 69.7% after CTS | Stopped after 24 hours in global routing: 296 overflows on Metal3 at 85% use |
| 7 | As build 6, `CTS_APPLY_NDR` `none` | 698,232 µm² | 63.7%, 69.7% after CTS | Failed: clock tree synthesis disconnected 3 row enables (critical disconnected pins) |
| 8 | Eight pages of latches, one clock-gating cell per row (RTL hash `87301e6a...a9e1`) | 696,141 µm² | 71.9% after CTS | Complete, DRC, LVS and antenna clean; slow-corner setup -2.005 ns |
| 9 | As build 8, long-wire buffering and wider repair margins after global routing | 696,141 µm² | 72.3% after CTS | Routed; slow-corner setup -2.116 ns, fast-corner hold -0.026 ns; stopped during Magic DRC |
| 10 | `out_raw` and `mask_n` shifters built from multiplexers (RTL hash `9eb0d482...65de`, the current RTL), 3 ns setup and 0.15 ns hold margins after global routing | 696,446 µm² | | Running |

Build 3 at 50 MHz, after detailed-route extraction:

| Corner | Setup worst slack | Failing setup endpoints | Hold worst slack |
| --- | --- | --- | --- |
| Fast, 1.32 V, -40 C | +4.397 ns | 0 | +0.110 ns |
| Typical, 1.20 V, 25 C | +3.769 ns | 0 | +0.290 ns |
| Slow, 1.08 V, 125 C | +0.126 ns | 0 | +0.610 ns |

Build 3 standard-cell area is 862,464 µm² (71.4% utilization), with 150,715 µm² of timing-repair buffers; Magic DRC, LVS and router DRC report 0 errors and no antenna violations.
It retains 419 fanout, up to 171 slew (slow corner) and 3 capacitance violations, unresolved.
Its RTL is not the current one: four pages cannot hold the error-confinement CAN node (B9).

Six pages of flip-flops do not leave the clock-tree buffers room to legalize at 75-79% utilization, so the store moved to latches (`sg13cmos5l_dlhrq_1`), which brings eight pages to about the four-page flip-flop build's area and utilization.
`tools/measure.py` with the CMOS5L typical library maps the current RTL to 1,032,387 µm² with flip-flop storage and 695,722 µm² with latches (8,704 latches: the 512 instruction words and the 64 G2 table rows), before placement.

Build 6's clock tree synthesis gives each latch row's gate, the registered decode ANDed with the inverted clock, its own tree: 3,594 clock nets.
LibreLane's default `CTS_APPLY_NDR` of `half` routes them with double spacing; global routing then spent about 12 hours per pass disabling the rule net by net and still ended congested, so the build was stopped in the repair step after global routing.
`src/config.json` now sets `CTS_APPLY_NDR` to `none`.

Build 7 with default clock routing rules finished global routing in 87 seconds with 27 overflows, and detailed routing converged to 0 violations.
The flow then stopped at its disconnected-pin check: the NOR3 cells driving the gates of instruction rows 193, 295 and 397 had each lost an input.
The synthesized netlist connected all three inputs; clock tree synthesis removed them.
Synthesis had folded the inverted clock into the address decode it shares between rows, so the clock passed through shared decode cells, and some row-gate cells received it on two inputs.
On silicon those rows could not have been written correctly.
The store now instantiates one clock-gating cell (`sg13cmos5l_lgcp_1`) per row, clocked by the inverted clock and enabled by the registered decode, so the decode carries no clock ([H2](spec.md#storage-implementation-h2)).
Build 8 synthesizes 576 of them (512 instruction rows and 64 G2 table rows), 8,704 latches and no inferred latch, in 2,091 µm² less area than build 6.
Build 7's timing repair after clock tree synthesis inserted 1,884 hold delay cells, mostly on the store's write data, which changes at the rising edge that closes the row being written.

Build 8 at 50 MHz, after detailed-route extraction:

| Corner | Setup worst slack | Failing setup endpoints | Hold worst slack |
| --- | --- | --- | --- |
| Fast, 1.32 V, -40 C | +4.175 ns | 0 | +0.015 ns |
| Typical, 1.20 V, 25 C | +3.448 ns | 0 | +0.172 ns |
| Slow, 1.08 V, 125 C | -2.005 ns | 54 | +0.437 ns |

Standard-cell area is 902,998 µm² (74.7% utilization), with 160 fanout, 136 slew (slow corner) and 2 capacitance violations; Magic DRC, LVS and antenna checks pass.
At 48 MHz (the step `OpenROAD.STAPostPNR` rerun with `CLOCK_PERIOD` 20.8333 on the same state), the slow corner is still -1.172 ns with 23 failing endpoints; hold is unchanged.
The failing paths are full-cycle paths in engine logic, not the store's half-cycle paths; the worst loses about 4.5 ns in two minimum-size cells driving long nets (a NOR2 on a 1.5 mm net at 3.3 ns slew, an AOI22 on a 0.92 mm net at 2.6 ns).
Repair after global routing found only 4 slew violations from its parasitic estimate; 610 nets are longer than 500 µm, and no maximum wire length was set.
Build 9 sets a 400 µm maximum wire length for design repair after placement and after global routing, 30% slew and capacitance margins after global routing, and a 1 ns setup margin after global routing (`src/config.json`); repair after placement then buffered 331 long nets with 378 buffers.

Build 9 at 50 MHz, after detailed-route extraction:

| Corner | Setup worst slack | Failing setup endpoints | Hold worst slack |
| --- | --- | --- | --- |
| Fast, 1.32 V, -40 C | +4.170 ns | 0 | -0.026 ns (3 endpoints) |
| Typical, 1.20 V, 25 C | +3.435 ns | 0 | +0.101 ns |
| Slow, 1.08 V, 125 C | -2.116 ns | 42 | +0.358 ns |

Standard-cell area is 932,001 µm² (77.1% utilization), with 144 fanout, 12 slew (slow corner) and 1 capacitance violation: wire-length buffering removed the slew violations but not the setup failure.
The parasitic estimate after global routing was about 3 ns optimistic: repair at that point found no path within its 1 ns margin.
Every failing path starts at an engine's `cur[13]` or `tx_level` and ends at its `cur` or `ir`, through the `out pc` target (`jmp_base` plus the bits `out` would shift out) and the 512-word fetch multiplexer.
Yosys's `share` pass, which LibreLane 3.1.0.dev3 runs unconditionally (its `SYNTH_SHARE_RESOURCES` variable is not read), had merged each engine's `in` shift, OSR advance and `out_raw` shift into one shifter whose operand multiplexer is selected by the decoded opcode, putting about 9 ns of opcode decoding and operand selection ahead of the `jmp_base` adder.
A 0.86 ns hold delay cell also sat on the decoding branch.
`out_raw` and its mask are now built from explicit multiplexer stages, which `share` does not merge; the shifts that remain shared feed only registers.

Gate-level simulation of build 8's routed netlist (`make GATES=yes` in `test/`, without timing) passes `test_every_instruction_word`, which fails on build 7's netlist at exactly words 193, 295 and 397, and one or more tests of each baseline protocol: `test_reset_state`, `test_page_jumps`, `test_instruction_pages`, `test_uart_tx_fast`, `test_uart_rx`, `test_spi_controller_mode0`, `test_i2c_controller_400khz`, `test_jtag_idcode_ir_dr_bypass`, `test_swd_read_write`, `test_ps2_chip_as_keyboard`, `test_ps2_chip_as_host`, `test_can_receive`, `test_can_node_transmit_error_counts`, `test_can_node_bus_integration`, `test_usb_get_descriptor`, `test_eth_receive` and `test_eth_transmit`.

## 2026-10-24: four-page memory and clock recovery, local 8x4 build

Source: working branch `issue-14-remaining`, based on `32b00c202aff6a85a6b1b14bcfbfc938e50ea11f`, with 256 flip-flop instruction words and `pe_cr.v` clock recovery.
All eleven RTL files were compared byte-for-byte with the build snapshot at `/tmp/pe-build/src`.
The SHA-256 of the sorted JSON map from RTL paths to their SHA-256 hashes is `26b8590ba3a6503e50cc127dad153713509246396940c81abcd3c92e28e60779`.
The local evidence is in `output/issue14/rtl-manifest.json`, `measure.json`, `post-route-state.json`, the timing reports and the 40/48 MHz STA subdirectories.
These are local generated artifacts, not published CI evidence.

Tool: LibreLane 3.1.0.dev3 in `ghcr.io/librelane/librelane:3.1.0.dev3`, CMOS5L PDK, and the 8x4 template from tt-support-tools `d66cf179e7bc4d296362ab7e2e3b344dc3c4f665`.
The build uses the existing 20 ns clock, 60% target placement density and 7 ns placement setup-repair margin.
Detailed routing, parasitic extraction and post-route STA have completed; final Magic DRC, LVS and the remaining flow checks were still running when this entry was first recorded.
This is not yet a passing hardening/precheck result.

| Measure | Value |
| --- | --- |
| Die (8x4) | 1724.16 x 710.64 µm, 1.225 mm² |
| Standard-cell area, excluding fill | 839,316 µm² |
| Standard-cell utilization | 69.47% |
| Timing-repair buffers | 145,373 µm² |
| Sequential cells | 300,209 µm² |
| Detailed-route DRC errors | 0 (router check; final Magic DRC is separate) |
| Antenna net/pin violations after repair | 0 / 0 |

At 50 MHz, after detailed-route extraction:

| Corner | Setup worst slack | Failing setup endpoints | Hold worst slack |
| --- | --- | --- | --- |
| Fast, 1.32 V, -40 C | +5.859 ns | 0 | +0.087 ns |
| Typical, 1.20 V, 25 C | +5.476 ns | 0 | +0.290 ns |
| Slow, 1.08 V, 125 C | -3.157 ns | 64 | +0.610 ns |

Slow-corner setup TNS is -162.729 ns.
The worst path starts at G2 output 1, passes through the output-pin/readback path and engine 1 fetch selection, and ends at its instruction register.
The reports also retain 427 fanout, up to 264 slew and up to 18 capacitance violations; these remain unresolved.

The same routed netlist and extracted parasitics were re-analyzed at 40 and 48 MHz, changing the SDC clock period while retaining its absolute 4 ns input/output delays, clock uncertainty and loads.
Both standalone STA runs completed successfully; their success means analysis completed, not that all timing constraints passed.

| Clock profile | Fast setup | Typical setup | Slow setup | Worst hold |
| --- | --- | --- | --- | --- |
| 40 MHz | +7.359 ns | +7.365 ns | +1.843 ns | +0.087 ns |
| 48 MHz | +6.109 ns | +6.115 ns | -2.324 ns | +0.087 ns |
| 50 MHz | +5.859 ns | +5.476 ns | -3.157 ns | +0.087 ns |

Only the 40 MHz profile passes setup and hold at every analyzed corner, and electrical violations still need resolution.
This cannot replace the 48 and 50 MHz profiles required by the existing USB and CAN programs.
The mandatory baseline and all-corner operating-clock requirement therefore remain incomplete.

Verification of this working source: `make check` passes both storage variants' lint and synthesis checks, the fetch/FIFO/reset formal checks, four tooling tests and all 121 RTL cocotb cases.
Five targeted functional gate-level cases pass on the post-route mapped netlist: instruction pages, page jumps, CAN transmit to a 1.5% fast peer, CAN underrun and recovery, and Ethernet receive jitter.
The netlist SHA-256 is `d077df2dae1deaedafe5af3781e123188513e2bd4f22d2eca5bd8e0e99a14bf5`.
These functional simulations have no delay annotation and do not establish timing closure; the full gate-level suite and Tiny Tapeout precheck remain acceptance gates.

## 2026-10-04: first complete RTL

Source: the first full implementation of `docs/spec.md` (uncommitted at the time of measurement).
Tool: yosys 0.69, `synth -flatten`, mapped with ABC to the IHP `sg13g2` typical liberty (1.2 V, 25 C).
The CMOS5L flow uses the same cell names, but its liberty was not available locally, so treat these numbers as estimates until the GDS action reports them.

| Build | Cell area | Share of a 0.70-0.75 mm² 6x4 allocation |
| --- | --- | --- |
| Flip-flop storage (default) | 0.326 mm² | 43-47% |
| Latch storage (`PE_LATCH_STORE`) | 0.274 mm² | 37-39% |

Breakdown of the flip-flop build, from a hierarchical run (sums to slightly more than the flat run):

| Block | Area (µm²) |
| --- | --- |
| Engine 0, including buffers and checksum logic | 84,000 |
| Engine 1, including buffers and checksum logic | 84,000 |
| Instruction memory storage | 69,500 |
| Instruction memory read multiplexers (2 ports) | 27,000 |
| Bit state machine table | 35,100 |
| Bit state machine logic | 10,200 |
| Pin block | 15,000 |
| Global registers and counter | 8,400 |
| Host port | 5,600 |

Sequential cells are 44% of the flip-flop build; the latch build replaces 1,536 storage flip-flops with `sg13g2_dlhq_1` latches.

Timing estimate: ABC `stime` reports a worst register-to-register logic delay of 5.4 ns, without wire load, at the typical corner.
This suggests margin against the 20 ns target, but it is not routed timing and excludes clock-to-output, setup, wires and slow corners.

Next measurement: the GDS action on CMOS5L, for real utilization, routing congestion and setup/hold slack.

## 2026-10-07: first routed CMOS5L build

Source: `main` at `9f2c041`, the two-engine design merged from the programmable emulator branch.
Tool: Tiny Tapeout GDS action for `ihp-sg13cmos5l` (LibreLane 3.1.0.dev3), [run 37629981691](https://github.com/JediNakDev/protocal-emulator-asic/actions/runs/37629981691), with the template's `src/config.json` (`CLOCK_PERIOD` 20 ns, `PL_TARGET_DENSITY_PCT` 60, `SYNTH_STRATEGY` `AREA 0`).
Precheck, DRC, LVS, antenna checks and the gate-level test passed.

| Measure | Value |
| --- | --- |
| Die (6x4 tiles) | 1289.28 x 710.64 µm, 0.916 mm² |
| Standard cell area | 427,748 µm², 47.4% utilization |
| Logic and flip-flops | 344,977 µm² (flip-flops 142,851) |
| Timing repair buffers | 73,853 µm², 6,402 cells |
| Clock tree | 8,063 µm² |
| Routed wire length | 1.95 m |

| Corner | Setup worst slack | Setup total negative slack | Failing endpoints | Hold worst slack |
| --- | --- | --- | --- | --- |
| Typical, 1.20 V, 25 C | +0.56 ns | 0 | 0 | +0.29 ns |
| Slow, 1.08 V, 125 C | -11.31 ns | -4,057 ns | 571 | +0.61 ns |
| Fast, 1.32 V, -40 C | +5.73 ns | 0 | 0 | +0.11 ns |

The design met 50 MHz only at the typical corner, with 0.56 ns to spare; at the slow corner it would run at about 31 MHz.
Tiny Tapeout signs off the typical corner only (`TIMING_VIOLATION_CORNERS` is `*typ*`), so the flow reported success.
Every failing path started at an engine PC and went through the instruction memory read multiplexer (about 10.7 ns at the slow corner) before decoding, then through the execute logic to the pin, X, ISR, buffer and checksum registers.
The pre-placement estimate above (5.4 ns of logic) missed most of this: weak fanout buffers on the PC and instruction bits and long wires across the die.
The flow also left 221 maximum fanout, 163 maximum slew and 10 maximum capacitance violations, and detailed routing took 2 hours 20 minutes of the 3 hour 12 minute run.

## 2026-10-08: timing closure work on `feat/timing-closure`

The fixes below are in the RTL; each routed build used the Tiny Tapeout GDS action, and each estimate the `timing` workflow, which stops before detailed routing.

| Change | What it removed |
| --- | --- |
| Fetch one cycle ahead (`ir`), snooping Host writes | The instruction memory read multiplexer from every path through decode (10.7 ns at the slow corner) |
| Decode from a register (`cur`) | The exec slot selection and its 2.8 ns fanout from the start of decode |
| Registered `CMD` pulses | A Host bus decode reaching every engine's issue logic through `step` |

Routed results, all with the template's `AREA 0` synthesis:

| Source | Slow setup | Failing endpoints | Typical setup | Fast setup | Hold, worst | Detailed routing |
| --- | --- | --- | --- | --- | --- | --- |
| `main` (`9f2c041`) | -11.31 ns | 571 | +0.56 ns | +5.73 ns | +0.11 ns | 2 h 20 min |
| Fetch ahead (`249d7aa`) | -11.02 ns | 540 | +0.48 ns | +5.74 ns | +0.11 ns | 4 h 36 min |
| Plus decode register (`c43b1e8`) | -10.51 ns | 489 | +0.95 ns | +5.61 ns | +0.11 ns | 2 h 49 min |

These look like little progress, but the reports show why: in the last build every one of the 489 failing endpoints had its worst path start at a Host bus address register and run through `step` into the engine's issue logic.
That path had been just behind the PC path on `main` too; registering the `CMD` pulses removes it, and the next routed build measures what remains.

Findings that shape the next steps:

- The estimate before detailed routing is optimistic: the same `c43b1e8` path took 23.4 ns estimated and 31.2 ns routed at the slow corner, and LibreLane's own mid-flow STA read +5.55 ns at the typical corner where the routed build reached +0.95 ns.
  Recomputing the parasitics from global routes did not change the estimate, so the difference comes from detailed routing itself.
- Signals may use only Metal2 to Metal4, so routing is congested: detailed routing alone took between 2 h 20 min and 4 h 36 min for nearly the same netlist.
- `SYNTH_STRATEGY` `DELAY 4` improved the estimate by about 8 ns at the slow corner for 8% more cell area (469,229 µm²), but that build did not finish detailed routing within GitHub's 6 hour job limit.
  Timing-driven placement with timing repair after global routing gave no gain beyond the spread between runs and doubled the time to global routing.

## 2026-10-10: current main with registered commands and setup margin

Source: `main` at `17995b3`, including the fetch/decode registers, registered Host commands, verification fixes, and waveform capture/replay programs.
Build: [GDS run 38056908515](https://github.com/JediNakDev/protocal-emulator-asic/actions/runs/38056908515) on CMOS5L, with `CLOCK_PERIOD` 20 ns, `PL_TARGET_DENSITY_PCT` 60, `SYNTH_STRATEGY` `AREA 0`, and `PL_RESIZER_SETUP_SLACK_MARGIN` 7 ns.
The GDS, precheck, functional gate-level test, and viewer jobs all passed.
The values below come from `tt_submission/stats/metrics.csv` in the run's `tt_submission` artifact; `commit_id.json` confirms the source commit.

| Measure | Value |
| --- | --- |
| Standard cell area | 436,690 µm², 48.39% utilization |
| Timing repair buffers | 76,914 µm² |
| Clock buffers and inverters | 7,949 µm² |
| Routed wire length | 2.02 m |

| Corner | Setup worst slack | Setup total negative slack | Hold worst slack |
| --- | --- | --- | --- |
| Typical, 1.20 V, 25 C | +4.05 ns | 0 | +0.29 ns |
| Slow, 1.08 V, 125 C | -4.14 ns | -94.37 ns | +0.61 ns |
| Fast, 1.32 V, -40 C | +5.81 ns | 0 | +0.11 ns |

The build improves slow-corner setup slack compared with the earlier routed results but still does not close 50 MHz at every corner.
The flow reports success because its timing sign-off filter is still `*typ*`.
Hold timing passes at all three corners.
Remaining electrical violations include 228 maximum fanout violations, up to 221 maximum slew violations, and up to 21 maximum capacitance violations.

The same source's [global-route timing estimate, run 38056908556](https://github.com/JediNakDev/protocal-emulator-asic/actions/runs/38056908556), reported +2.62 ns slow-corner setup slack and 435,792 µm² of standard cell area.
Detailed routing changed that slow-corner result to -4.14 ns, a 6.76 ns loss of margin.
Use the final routed reports when assessing operating limits.

## 2026-10-15: 128-word instruction memory in two pages

Source: branch `design-review-baseline`, after the 128-word paged instruction memory and G2 emission into either engine.
Tool: yosys 0.69, `synth -flatten -top tt_um_jedinakdev_protocol_emulator`, mapped with ABC to the IHP `sg13g2` typical liberty (1.2 V, 25 C); cell area before placement.

| Store | Flip-flop storage | Latch storage (`PE_LATCH_STORE`) |
| --- | --- | --- |
| 64 words (before) | 332,569 µm² | 280,354 µm² |
| 128 words (this change) | 429,914 µm² | 341,332 µm² |

Doubling the store adds 97,345 µm² (+29%) to the flip-flop build.
Scaling the routed `17995b3` result (436,690 µm² at 48.39%) by the same growth projects roughly 62% utilization on 6x4.
This is an estimate; the next routed prerelease must confirm utilization, congestion and slow-corner setup slack.
