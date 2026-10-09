# Formal checks

`make formal` (part of `make check`) proves these properties with `yosys-smtbmc` and `z3`.
Install Z3 alongside Yosys as described in the root README.
Each run regenerates the SMT model and stops on synthesis errors before invoking the solver, including when an older model already exists.
Each harness instantiates RTL from `../src` and states its property in terms of `docs/spec.md`.

| File | Property | Method |
| --- | --- | --- |
| `fetch.v` | The engine's `ir` always holds the instruction memory word at PC, and with the exec slot empty `cur` equals `ir`, under any Host writes, restarts, steps, forced instructions, pins, flags and bit state machine activity | Induction step: from any state that satisfies it, the next state does |
| `fifo.v` | A queue never holds more words than its buffer mode gives it entries, whatever the Host and the engine do | Induction step |
| `reset.v` | While `rst_n` is low, and in the two cycles in which the reset synchronizer releases, every output is 0 | Bounded check from any power-up state |

The fetch property is the correctness argument for fetching one cycle ahead: the rest of the engine decodes `cur`, so it executes exactly the word an engine reading the memory in the cycle it issues would.
It holds from the moment the Host has written the word at PC, or the engine has jumped, as the specification requires before an engine is enabled.

Each property was checked against deliberate RTL mutations that break it, such as removing the Host write snoop, so the proofs are not vacuous.
The engine exposes its fetch registers through ports that exist only when `FORMAL` is defined.
