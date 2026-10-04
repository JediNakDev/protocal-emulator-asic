/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Shared instruction memory: 64 x 16 bits, one Host write port and one
// combinational read port per engine.
module pe_imem (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        we,
    input  wire [5:0]  waddr,
    input  wire [15:0] wdata,
    input  wire [5:0]  raddr0,
    output wire [15:0] rdata0,
    input  wire [5:0]  raddr1,
    output wire [15:0] rdata1
);

  wire [64*16-1:0] q;

  pe_store #(
      .W (16),
      .D (64),
      .AW(6)
  ) u_store (
      .clk  (clk),
      .rst_n(rst_n),
      .we   (we),
      .waddr(waddr),
      .wdata(wdata),
      .q    (q)
  );

  assign rdata0 = q[raddr0*16 +: 16];
  assign rdata1 = q[raddr1*16 +: 16];

endmodule
