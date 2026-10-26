/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Write-only storage array with every word exposed, so callers build their
// own read multiplexers. Reset clears every word to 0.
//
// Default (tapeout): one latch with reset per bit, about 40% of the area of
// a flip-flop with its write-enable multiplexer on IHP CMOS5L (H2 in
// docs/spec.md). The write is registered; in the following cycle the
// addressed row is transparent while clk is low, so the word is stable
// before the next rising edge. Synthesis instantiates the library latch
// sg13cmos5l_dlhrq_1 and the clock gate sg13cmos5l_lgcp_1 directly, so no
// latch is inferred; simulation uses equivalent behavioral models.
//
// With PE_FLOP_STORE defined: one flip-flop per bit, written at the end of
// the cycle that carries the write.
module pe_store #(
    parameter integer W  = 16,
    parameter integer D  = 64,
    parameter integer AW = 6
) (
    input  wire          clk,
    input  wire          rst_n,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [W-1:0]  wdata,
    output wire [W*D-1:0] q
);

`ifdef PE_FLOP_STORE
  genvar i;
  generate
    for (i = 0; i < D; i = i + 1) begin : g_row
      reg [W-1:0] row;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) row <= {W{1'b0}};
        else if (we && waddr == i[AW-1:0]) row <= wdata;
      end
      assign q[i*W +: W] = row;
    end
  endgenerate
`else
  reg          we_q;
  reg [AW-1:0] wa_q;
  reg [W-1:0]  wd_q;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      we_q <= 1'b0;
      wa_q <= {AW{1'b0}};
      wd_q <= {W{1'b0}};
    end else begin
      we_q <= we;
      if (we) begin
        wa_q <= waddr;
        wd_q <= wdata;
      end
    end
  end

  wire clk_n = ~clk;

  genvar i, b;
  generate
    for (i = 0; i < D; i = i + 1) begin : g_row
      // One clock gate per row: high while clk is low in the cycle after a
      // write to the row. The gate cell latches the row select while clk is
      // high, so a decode that settles after the rising edge cannot open a
      // row, and the clock reaches only the gate cells, never the shared
      // address decode.
      wire sel = we_q & (wa_q == i[AW-1:0]);
      wire gate;
`ifdef SYNTHESIS
      sg13cmos5l_lgcp_1 u_gate (
          .GCLK(gate),
          .CLK (clk_n),
          .GATE(sel)
      );
`else
      assign gate = clk_n & sel;
`endif
      for (b = 0; b < W; b = b + 1) begin : g_bit
`ifdef SYNTHESIS
        sg13cmos5l_dlhrq_1 u_latch (
            .Q      (q[i*W + b]),
            .D      (wd_q[b]),
            .GATE   (gate),
            .RESET_B(rst_n)
        );
`else
        reg bit_q;
        /* verilator lint_off LATCH */
        always @(*) begin
          if (!rst_n) bit_q = 1'b0;
          else if (gate) bit_q = wd_q[b];
        end
        /* verilator lint_on LATCH */
        assign q[i*W + b] = bit_q;
`endif
      end
    end
  endgenerate
`endif

endmodule
