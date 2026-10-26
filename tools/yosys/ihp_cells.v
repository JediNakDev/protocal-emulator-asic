// SPDX-License-Identifier: Apache-2.0
// Port declarations of the IHP CMOS5L library cells that the RTL instantiates
// in synthesis (`ifdef SYNTHESIS`), so `make synth-check` can check that path
// without the PDK. LibreLane uses the library itself.
(* blackbox *)
module sg13cmos5l_dlhrq_1 (Q, D, GATE, RESET_B);
  output Q;
  input  D, GATE, RESET_B;
endmodule

(* blackbox *)
module sg13cmos5l_lgcp_1 (GCLK, CLK, GATE);
  output GCLK;
  input  CLK, GATE;
endmodule
