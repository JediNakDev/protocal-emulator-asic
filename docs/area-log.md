# Area and timing log

Each entry records the source state, how the numbers were produced, and what they mean.
Numbers from yosys are cell area before placement; routing, clock tree and fill are not included.

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
