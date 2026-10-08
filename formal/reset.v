/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Reset state (H5 in docs/spec.md), from any power-up state and with any
// input activity: while rst_n is low, and in the two cycles in which the
// reset synchronizer releases after it rises, every output is 0, so all
// bidirectional pins are released.
module reset_props (
    input wire       clk,
    input wire       rst_n,
    input wire [7:0] ui_in,
    input wire [7:0] uio_in
);

  wire [7:0] uo_out, uio_out, uio_oe;

  tt_um_jedinakdev_protocol_emulator dut (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (1'b1),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // Cycles since rst_n was last low, saturating at 3.
  reg [1:0] since = 2'd0;
  always @(posedge clk) since <= !rst_n ? 2'd0 : (since == 2'd3 ? since : since + 2'd1);

  reg first = 1'b1;
  always @(posedge clk) first <= 1'b0;

  always @(*) begin
    if (first) assume (!rst_n);
    if (!rst_n || since < 2'd2) assert (uo_out == 8'd0 && uio_out == 8'd0 && uio_oe == 8'd0);
  end

endmodule
