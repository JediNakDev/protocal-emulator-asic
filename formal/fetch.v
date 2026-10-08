/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Induction step for the engine's fetch stage (docs/spec.md, instruction
// memory). Every input is free: Host register writes, restarts, steps,
// forced instructions, pins, flags and the bit state machine. From any state
// where `ir` is the memory word at PC and an empty exec slot leaves `cur`
// equal to `ir`, the next state is one too. The rest of the engine decodes
// `cur`, so it executes exactly the word an engine reading the memory in the
// cycle it issues would.
module fetch_props (
    input wire        clk,
    input wire [6:0]  bus_addr,
    input wire [7:0]  bus_wdata,
    input wire [7:0]  bus_lo_hold,
    input wire        bus_hi,
    input wire        bus_we,
    input wire        bus_commit,
    input wire        bus_fetch,
    input wire        enable,
    input wire        restart,
    input wire        step,
    input wire        fifo_clear,
    input wire        div_sync,
    input wire [2:0]  dbg_sel,
    input wire        imem_we,
    input wire [5:0]  imem_waddr,
    input wire [15:0] imem_wdata,
    input wire [15:0] pins_in,
    input wire [7:0]  flags,
    input wire [31:0] counter,
    input wire        g2_owner,
    input wire        g2_feed_full,
    input wire        g2_emit,
    input wire        g2_emit_bit
);

  reg [15:0] mem [0:63];
  always @(posedge clk) if (imem_we) mem[imem_waddr] <= imem_wdata;

  wire [5:0]  fetch_addr, pc;
  wire [15:0] ir, cur;
  wire        exec_valid;

  pe_engine #(
      .BLOCK(2'b10)
  ) dut (
      .clk         (clk),
      .rst_n       (1'b1),
      .bus_addr    (bus_addr),
      .bus_wdata   (bus_wdata),
      .bus_lo_hold (bus_lo_hold),
      .bus_hi      (bus_hi),
      .bus_we      (bus_we),
      .bus_commit  (bus_commit),
      .bus_fetch   (bus_fetch),
      .bus_rdata   (),
      .enable      (enable),
      .restart     (restart),
      .step        (step),
      .fifo_clear  (fifo_clear),
      .div_sync    (div_sync),
      .dbg_sel     (dbg_sel),
      .fetch_addr  (fetch_addr),
      .imem_data   (mem[fetch_addr]),
      .imem_we     (imem_we),
      .imem_waddr  (imem_waddr),
      .imem_wdata  (imem_wdata),
      .pins_in     (pins_in),
      .pin_out     (),
      .pin_dir     (),
      .flags       (flags),
      .flag_set    (),
      .flag_clr    (),
      .counter     (counter),
      .g2_owner    (g2_owner),
      .g2_feed_full(g2_feed_full),
      .g2_feed_wr  (),
      .g2_feed_bit (),
      .g2_emit     (g2_emit),
      .g2_emit_bit (g2_emit_bit),
      .div_tick    (),
      .stalled     (),
      .rx_nonempty (),
      .tx_notfull  (),
      .st_rx_ovf   (),
      .st_host_tx_ovf(),
      .st_host_rx_unf(),
      .st_cap_ovr  (),
      .f_pc        (pc),
      .f_ir        (ir),
      .f_cur       (cur),
      .f_exec_valid(exec_valid)
  );

  wire inv = (ir == mem[pc]) && (exec_valid || cur == ir);

  reg first = 1'b1;
  always @(posedge clk) first <= 1'b0;

  always @(*) begin
    if (first) assume (inv);
    else assert (inv);
  end

endmodule
