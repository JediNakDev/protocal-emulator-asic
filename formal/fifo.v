/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Induction step for the buffers (G5): a queue never holds more words than
// its mode gives it entries (docs/spec.md), whatever the Host and the engine
// do. As in the engine, the mode changes only together with a clear.
module fifo_props (
    input wire        clk,
    input wire        clear,
    input wire [2:0]  mode,
    input wire        h_tx_push,
    input wire [15:0] h_tx_data,
    input wire        h_rx_pop,
    input wire        h_ram_we,
    input wire [2:0]  h_ram_idx,
    input wire [15:0] h_ram_wdata,
    input wire        e_tx_pop,
    input wire        e_rx_push,
    input wire [15:0] e_rx_data,
    input wire        e_ram_we,
    input wire [2:0]  e_ram_idx,
    input wire [15:0] e_ram_wdata
);

  wire [3:0] tx_level, rx_level;

  pe_fifo dut (
      .clk        (clk),
      .rst_n      (1'b1),
      .clear      (clear),
      .mode       (mode),
      .h_tx_push  (h_tx_push),
      .h_tx_data  (h_tx_data),
      .h_rx_pop   (h_rx_pop),
      .h_rx_data  (),
      .h_ram_we   (h_ram_we),
      .h_ram_idx  (h_ram_idx),
      .h_ram_wdata(h_ram_wdata),
      .h_ram_rdata(),
      .h_tx_drop  (),
      .h_rx_unf   (),
      .e_tx_pop   (e_tx_pop),
      .e_tx_data  (),
      .e_rx_push  (e_rx_push),
      .e_rx_data  (e_rx_data),
      .e_ram_we   (e_ram_we),
      .e_ram_idx  (e_ram_idx),
      .e_ram_wdata(e_ram_wdata),
      .e_ram_rdata(),
      .tx_level   (tx_level),
      .rx_level   (rx_level),
      .tx_empty   (),
      .tx_full    (),
      .rx_empty   (),
      .rx_full    ()
  );

  // Entries per queue, from the mode table in docs/spec.md.
  function [3:0] tx_entries;
    input [2:0] m;
    case (m)
      3'd1: tx_entries = 4'd8;
      3'd2, 3'd4, 3'd5: tx_entries = 4'd0;
      default: tx_entries = 4'd4;
    endcase
  endfunction

  function [3:0] rx_entries;
    input [2:0] m;
    case (m)
      3'd2: rx_entries = 4'd8;
      3'd1, 3'd3, 3'd5: rx_entries = 4'd0;
      default: rx_entries = 4'd4;
    endcase
  endfunction

  wire inv = (tx_level <= tx_entries(mode)) && (rx_level <= rx_entries(mode));

  reg       first = 1'b1;
  reg [2:0] prev_mode;
  reg       prev_clear;
  always @(posedge clk) begin
    first      <= 1'b0;
    prev_mode  <= mode;
    prev_clear <= clear;
  end

  always @(*) begin
    if (first) begin
      assume (inv);
    end else begin
      assume (mode == prev_mode || prev_clear);
      assert (inv);
    end
  end

endmodule
