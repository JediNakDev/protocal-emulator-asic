/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Write-only storage array with every word exposed, so callers build their
// own read multiplexers. Contents are not reset.
//
// Default: flip-flops. With PE_LATCH_STORE defined: one latch per bit. The
// write is registered, then the addressed row is transparent while clk is low
// in the following cycle. The latch build is unverified on IHP CMOS5L (H2 in
// docs/spec.md) and is not used for tapeout.
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

`ifdef PE_LATCH_STORE
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

  genvar i;
  generate
    for (i = 0; i < D; i = i + 1) begin : g_row
      wire          gate = we_q & (wa_q == i[AW-1:0]) & ~clk;
      reg  [W-1:0]  row;
      /* verilator lint_off LATCH */
      always @(*) begin
        if (gate) row = wd_q;
      end
      /* verilator lint_on LATCH */
      assign q[i*W +: W] = row;
    end
  endgenerate
`else
  wire _unused = rst_n;

  genvar i;
  generate
    for (i = 0; i < D; i = i + 1) begin : g_row
      reg [W-1:0] row;
      always @(posedge clk) begin
        if (we && waddr == i[AW-1:0]) row <= wdata;
      end
      assign q[i*W +: W] = row;
    end
  endgenerate
`endif

endmodule
