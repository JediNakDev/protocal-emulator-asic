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
