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
