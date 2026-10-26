/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Per-engine buffers (G5): 8 x 16 bits shared between a transmit queue
// (Host to engine), a receive queue (engine to Host) and RAM, per mode.
module pe_fifo (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        clear,
    input  wire [2:0]  mode,
    // Host side
    input  wire        h_tx_push,
    input  wire [15:0] h_tx_data,
    input  wire        h_rx_pop,
    output wire [15:0] h_rx_data,
    input  wire        h_ram_we,
    input  wire [2:0]  h_ram_idx,
    input  wire [15:0] h_ram_wdata,
    output wire [15:0] h_ram_rdata,
    output wire        h_tx_drop,
    output wire        h_rx_unf,
    // Engine side
    input  wire        e_tx_pop,
    output wire [15:0] e_tx_data,
    input  wire        e_rx_push,
    input  wire [15:0] e_rx_data,
    input  wire        e_ram_we,
    input  wire [2:0]  e_ram_idx,
    input  wire [15:0] e_ram_wdata,
    output wire [15:0] e_ram_rdata,
    // Levels
    output wire [3:0]  tx_level,
    output wire [3:0]  rx_level,
    output wire        tx_empty,
    output wire        tx_full,
    output wire        rx_empty,
    output wire        rx_full
);


  // ------------------------------------------------------------ mode decode
  reg [3:0] tx_depth, rx_depth;
  reg       rx_base4;
  reg [7:0] ram_mask;

  always @(*) begin
    tx_depth = 4'd4;
    rx_depth = 4'd4;
    rx_base4 = 1'b1;
    ram_mask = 8'h00;
    case (mode)
      3'd1: begin tx_depth = 4'd8; rx_depth = 4'd0; end
      3'd2: begin tx_depth = 4'd0; rx_depth = 4'd8; rx_base4 = 1'b0; end
      3'd3: begin rx_depth = 4'd0; ram_mask = 8'hF0; end
      3'd4: begin tx_depth = 4'd0; ram_mask = 8'h0F; end
      3'd5: begin tx_depth = 4'd0; rx_depth = 4'd0; ram_mask = 8'hFF; end
      default: ;
    endcase
  end

  // --------------------------------------------------------------- pointers
  reg [2:0] tx_wp, tx_rp, rx_wp, rx_rp;
  reg [3:0] tx_cnt, rx_cnt;

  assign tx_level = tx_cnt;
  assign rx_level = rx_cnt;
  assign tx_empty = (tx_cnt == 4'd0);
  assign tx_full  = (tx_cnt == tx_depth);
  assign rx_empty = (rx_cnt == 4'd0);
  assign rx_full  = (rx_cnt == rx_depth);

  wire tx_push = h_tx_push & ~tx_full;
  wire tx_pop  = e_tx_pop & ~tx_empty;
  wire rx_push = e_rx_push & ~rx_full;
  wire rx_pop  = h_rx_pop & ~rx_empty;

  assign h_tx_drop = h_tx_push & tx_full;
  assign h_rx_unf  = h_rx_pop & rx_empty;

  function [2:0] phys;
    input [2:0] ptr;
    input [3:0] depth;
    input       base4;
    begin
      if (depth == 4'd8) phys = ptr;
      else phys = {base4, ptr[1:0]};
    end
  endfunction

  wire [2:0] tx_wa = phys(tx_wp, tx_depth, 1'b0);
  wire [2:0] tx_ra = phys(tx_rp, tx_depth, 1'b0);
  wire [2:0] rx_wa = phys(rx_wp, rx_depth, rx_base4);
  wire [2:0] rx_ra = phys(rx_rp, rx_depth, rx_base4);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      tx_wp  <= 3'd0;
      tx_rp  <= 3'd0;
      rx_wp  <= 3'd0;
      rx_rp  <= 3'd0;
      tx_cnt <= 4'd0;
      rx_cnt <= 4'd0;
    end else if (clear) begin
      tx_wp  <= 3'd0;
      tx_rp  <= 3'd0;
      rx_wp  <= 3'd0;
      rx_rp  <= 3'd0;
      tx_cnt <= 4'd0;
      rx_cnt <= 4'd0;
    end else begin
      if (tx_push) tx_wp <= tx_wp + 3'd1;
      if (tx_pop) tx_rp <= tx_rp + 3'd1;
      if (rx_push) rx_wp <= rx_wp + 3'd1;
      if (rx_pop) rx_rp <= rx_rp + 3'd1;
      tx_cnt <= tx_cnt + {3'd0, tx_push} - {3'd0, tx_pop};
      rx_cnt <= rx_cnt + {3'd0, rx_push} - {3'd0, rx_pop};
    end
  end

  // ---------------------------------------------------------------- storage
  wire [127:0] mem;   // entry n in bits 16n+15:16n

  // Engine writes win over Host writes to the same entry. Reset clears every
  // entry (see pe_store.v for why the storage is reset).
  genvar g;
  generate
    for (g = 0; g < 8; g = g + 1) begin : g_entry
      wire e_we = (rx_push && rx_wa == g[2:0]) || (e_ram_we && ram_mask[g] && e_ram_idx == g[2:0]);
      wire h_we = (tx_push && tx_wa == g[2:0]) || (h_ram_we && ram_mask[g] && h_ram_idx == g[2:0]);
      reg  [15:0] q;
      always @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 16'h0000;
        else if (e_we) q <= (rx_push && rx_wa == g[2:0]) ? e_rx_data : e_ram_wdata;
        else if (h_we) q <= (tx_push && tx_wa == g[2:0]) ? h_tx_data : h_ram_wdata;
      end
      assign mem[g*16 +: 16] = q;
    end
  endgenerate

  assign e_tx_data   = mem[tx_ra*16 +: 16];
  assign h_rx_data   = rx_empty ? 16'h0000 : mem[rx_ra*16 +: 16];
  assign e_ram_rdata = mem[e_ram_idx*16 +: 16];
  assign h_ram_rdata = mem[h_ram_idx*16 +: 16];

endmodule
