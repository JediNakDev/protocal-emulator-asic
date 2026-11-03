# Waveform demo

`make demo` shows the chip's distinctive capability from a clean checkout: it records a UART stream on one pin, the Host flips one bit of the recording, the chip replays the edited waveform on another pin, and an independent decoder shows the damaged byte.
Everything goes through the top-level pins and the Host port on the simulated chip.

This is RTL simulation only, at the 50 MHz profile with Icarus Verilog.
No part of this demo has run at gate level, on an FPGA or on silicon.

## Run

```sh
make setup
make demo
```

`make setup` needs [uv](https://docs.astral.sh/uv/getting-started/installation/), and the simulation needs Icarus Verilog ([README](../README.md#run-the-checks)).
A run takes a few seconds.

`make demo` runs `tools/demo_waveform.py`, which runs `test/demo_waveform.py` under cocotb and prints the report of that run.

| Output | Location, gitignored |
| --- | --- |
| Pin 8 and pin 10 traces, for GTKWave or any VCD viewer | `output/demo/waveform.vcd` |
| Everything the steps measured | `output/demo/report.json` |
| Simulator log | `output/demo/sim.log` |
| Build folder and cocotb results | `test/sim_build/demo/` |

The build folder, the results file and `WAVES=0` keep the demo apart from a plain `make test`, so `test/tb.fst`, `test/results.xml` and `test/sim_build/rtl` stay untouched.
Use `--sim-build` and `--out` on the tool to choose other folders.
The VCD holds the two pins only, not internal signals.

## Expected output

The simulation is deterministic, so a passing run prints this, apart from the path on the last line:

```text
Waveform capture, Host edit and replay: RTL simulation through the chip's pins (50 MHz profile)

== 1. Original: UART driven onto a pin, captured with timestamps ==
Driven   pin 8 (ui[6]), 8N1 at 250000 baud, 200 cycles per bit: 4F 4E "ON"
Captured initial level 1, 12 edges, STICKY 0x00
  edge  counter (cycles)  to level  after (cycles)
     0              1741         0  
     1              1941         1             200
     2              2741         0             800
     3              3141         1             400
     4              3341         0             200
     5              3541         1             200
     6              4141         0             600
     7              4541         1             400
     8              5141         0             600
     9              5541         1             400
    10              5741         0             200
    11              5941         1             200
Check    every interval equals pin 8 to the cycle (20 ns): yes

== 2. Edit: what the Host changed in the recording, and why ==
Why      8N1 has no parity, so a flipped data bit passes framing unnoticed; byte 1 bit 3 turns "ON" into "OF"
Change   invert cycles 4941 to 5141, that bit's 200 cycles
Removed  edge at [5141]
Added    edge at [4941]
             S01234567P  S01234567P  
captured   --_----__-_---__---__-_---
edited     --_----__-_---__--___-_---

== 3. Replay: waveform driven out of the chip ==
Pin 10 (uo[5]): the untouched recording as a control, then the edited one
Control  13 replay words, 12 edges, 137.7 to 236.3 us in the VCD; every interval equals its recording to the cycle: yes
Fault    13 replay words, 12 edges, 236.3 to 335.0 us in the VCD; every interval equals its recording to the cycle: yes
Words    fault replay: 018B 018A 063B 031A 018B 018A 04AB 031A 031B 04AA 018B 018A 0001
STICKY   0x00
             S01234567P  S01234567P  
control    --_----__-_---__---__-_---
fault      --_----__-_---__--___-_---

== 4. Decoded effect: independent UART decoder on the pins ==
Before   pin 8           4F 4E  "ON"
Control  pin 10 unedited 4F 4E  "ON"
After    pin 10 edited   4F 46  "OF"
Fault    byte 1: 4E -> 46, XOR 08
Framing  no violations: start and stop bits stay valid, so a receiver accepts the wrong byte

PASS: pin 8 and pin 10 traces in <repo>/output/demo/waveform.vcd; report.json and sim.log beside it
```

The bit rows show the line once per UART bit: `-` high, `_` low, `S` the start bit, `0` to `7` data bits least significant first, `P` the stop bit.

## What each step shows

1. **Original.** A test peer drives two UART bytes onto pin 8 while `capture.pasm` runs on engine 0 and the Host drains its timestamps.
   The table is the chip's own 32-bit cycle counter at each edge, and each interval must equal what a separate recording of the pin saw.
2. **Edit.** The Host calls `waveform.invert_span` on the recording, inverting the 200 cycles of byte 1's data bit 3.
   That bit was high and its neighbour after it low, so one falling edge moves 200 cycles earlier and the edge count stays 12.
   No RTL, program or chip setting changes; the only input is a different list of times.
3. **Replay.** `replay.pasm` on engine 1 plays the untouched recording, then the edited one, on pin 10.
   The control shows the capture and replay path changes nothing by itself, so any change in step 4 comes from the edit.
   A separate recording of the pin must match each recording's intervals to the cycle.
4. **Decoded effect.** `uart_decode` from `test/peers.py` samples each pin recording in absolute time at the nominal baud rate.
   It reads only those recordings, and shares no code with the programs or `tools/pe/proto/waveform.py`.
   The decoded fault is silent: the stop bit and framing are valid, and a device told `ON` hears `OF`.

## Exit status

`make demo` exits nonzero unless every one of these holds:

- The cocotb results file exists, holds exactly one testcase, and that testcase has no failure, error or skip.
- The report carries this run's id and has all four steps, so a leftover file cannot pass for a new run.
- `make` exited 0.

The tool deletes the old report, trace, log and results file before each run.
The simulation fails if captured or replayed intervals differ, a loss flag is set, the decoder disagrees with the bytes sent, the control decodes differently, or the edited replay decodes the same as the original.

A no-op edit, replacing the `invert_span` call with an unchanged recording, ends like this and exits 2 from `make`:

```text
After    pin 10 edited   4F 4E  "ON"
Fault    none: the bytes are identical

FAIL: see <repo>/output/demo/sim.log
  AssertionError: decoder saw no change: the edit injected no fault
assert [79, 78] != [79, 78]
  demo_waveform: failure
  make exited 2
```

## Limits

Times are at 50 MHz, where one cycle is 20 ns.
The Host in the measured rows is the cocotb Host of `test/chip.py`: 4-bit mode, `HSCK = f_clk / 8`, one `LEVELS` poll and then a burst.
A slower Host, or one with other work, sustains less.

| Limit | Value | Source |
| --- | --- | --- |
| Resolution | 1 cycle (20 ns) for capture and replay | [protocols.md](protocols.md#waveform-capture-and-replay) |
| Shortest captured pulse | 8 cycles (160 ns), enforced by the required 8-cycle glitch filter; shorter pulses are filtered out | `test_capture_minimum_burst_all_phases` |
| Shortest replayed level | 3 cycles (60 ns) | `programs/replay.pasm`; measured on pin 10 once, seven intervals of exactly 3 |
| Longest replay word | 32,770 cycles; `to_replay` splits longer runs | `programs/replay.pasm`, `tools/pe/proto/waveform.py` |
| Largest gap between edges | Under 2^32 cycles (86 s) | `waveform.from_capture` |
| Edit | Every run after the edit must stay at least 3 cycles, or `to_replay` raises an error | `tools/pe/proto/waveform.py` |
| Capture burst | 4 edges at the 8-cycle spacing fill the 8-word receive queue; each edge costs 5 cycles of the capture loop and two words | [protocols.md](protocols.md#waveform-capture-and-replay) |
| Sustained capture, continuous read | One edge per 80 cycles (1.6 µs) at `HSCK = f_clk / 10` | `test_capture_sustained_rate` |
| Sustained capture, polled | One edge per 88 cycles (1.76 µs) held over 300 edges; at 86 cycles per edge, words were lost and `STICKY` bit 0 set | One-off sweep on this RTL, not a regression test |
| Sustained replay, polled | One word per 43 cycles (0.86 µs) held over 300 words; at 42 cycles per word, 35 intervals stretched and no flag was set | One-off sweep on this RTL, not a regression test |
| Replay buffering | 8-word transmit queue; each edge costs one word | `programs/replay.pasm` |
| Capture with other engine traffic | The port is shared: capture of a 1 Mbit/s CAN bus overflows, and Ethernet receive beside capture lost 3 of 103 words at sweep traffic | [concurrency.md](concurrency.md) |

This demo's shortest spacing is 200 cycles per UART bit, the rate of `test_edit_and_replay_uart`, which leaves margin on both polled rates.

### Loss flags

| `STICKY` bit | Meaning here |
| --- | --- |
| 0, receive overflow | A timestamp word was dropped on a full receive queue |
| 6, capture overrun | An edge overwrote one the program had not read |
| 3, engine 1 transmit overflow | The Host wrote a full replay queue, and the word was dropped |

Discard a recording if bit 0 or 6 is set: timestamps may be incomplete or repeated.
`record` in `test/test_waveform.py` checks both bits before it returns, and the demo prints `STICKY` after capture and after replay.

### Replay underrun

Replay underrun has no flag.
An empty transmit queue stretches the current level until the next word arrives.
In a one-off measurement, a level meant to hold 100 cycles held 3,060 while the queue was dry, and `STICKY` stayed `0x00`.
The Host has to keep the queue fed, and only a recording of the pin shows a miss; the demo's cycle-exact interval check is that recording.

Pin 10 reads 0 until the first replay word.
The demo pushes one idle-level word first, so the line is at its idle level before a recording starts.
