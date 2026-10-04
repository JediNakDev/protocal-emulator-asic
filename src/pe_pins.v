/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Pin block: input synchronizers, glitch filters and falling-edge sampling
// (G6), per-pin owner and drive mode, and the 16-entry pin space.
module pe_pins (
    input  wire        clk,
    input  wire        rst_n,
    // Host register bus
    input  wire [6:0]  bus_addr,
    input  wire [7:0]  bus_wdata,
    input  wire        bus_we,
    output reg  [7:0]  bus_rdata,
    // Pads
    input  wire [7:0]  uio_in,
    input  wire [1:0]  ui_pins,
    output reg  [7:0]  uio_out,
    output reg  [7:0]  uio_oe,
    output reg  [2:0]  uo_pins,
    // Owners
    input  wire [12:0] e0_out,
    input  wire [12:0] e0_dir,
    input  wire [12:0] e1_out,
    input  wire [12:0] e1_dir,
    input  wire [1:0]  g2_out,
    // Pin space
    output wire [15:0] pins_in
);

  integer i;

  reg [7:0] cfg [0:12];
  reg [3:0] negsel;

  wire wr = bus_we & (bus_addr[6:5] == 2'b01) & (bus_addr[4:0] < 5'd13);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      for (i = 0; i < 13; i = i + 1) cfg[i] <= 8'h00;
      negsel <= 4'd0;
    end else begin
      if (wr) cfg[bus_addr[3:0]] <= bus_wdata;
      if (bus_we && bus_addr == 7'h12) negsel <= bus_wdata[3:0];
    end
  end

  // ---------------------------------------------------------------- inputs
  wire [9:0] raw = {ui_pins, uio_in};
  reg  [9:0] sync1, sync2;
  reg  [9:0] filt;
  reg  [2:0] fcnt [0:9];
  reg  [9:0] path;      // after the synchronizer or bypass
  reg  [9:0] in_val;    // after the filter

  always @(*) begin
    for (i = 0; i < 10; i = i + 1) begin
      path[i]   = cfg[i][5] ? raw[i] : sync2[i];
      in_val[i] = (cfg[i][7:6] == 2'd0) ? path[i] : filt[i];
    end
  end

  function [2:0] filt_last;  // count value at which a change is accepted
    input [1:0] code;
    begin
      case (code)
        2'd1: filt_last = 3'd1;
        2'd2: filt_last = 3'd3;
        default: filt_last = 3'd7;
      endcase
    end
  endfunction

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sync1 <= 10'd0;
      sync2 <= 10'd0;
      filt  <= 10'd0;
      for (i = 0; i < 10; i = i + 1) fcnt[i] <= 3'd0;
    end else begin
      sync1 <= raw;
      sync2 <= sync1;
      for (i = 0; i < 10; i = i + 1) begin
        if (path[i] == filt[i]) begin
          fcnt[i] <= 3'd0;
        end else if (fcnt[i] == filt_last(cfg[i][7:6])) begin
          filt[i] <= path[i];
          fcnt[i] <= 3'd0;
        end else begin
          fcnt[i] <= fcnt[i] + 3'd1;
        end
      end
    end
  end

  // Falling-edge sample of one pin, retimed to the rising edge.
  reg neg1, neg2;
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) neg1 <= 1'b0;
    else neg1 <= (negsel < 4'd10) ? raw[negsel] : 1'b0;
  end
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) neg2 <= 1'b0;
    else neg2 <= neg1;
  end

  // --------------------------------------------------------------- outputs
  reg [12:0] drv_val;   // value that reaches the pad
  reg [12:0] drv_oe;
  reg        v, d;

  always @(*) begin
    for (i = 0; i < 13; i = i + 1) begin
      case (cfg[i][1:0])
        2'd0: begin v = e0_out[i]; d = e0_dir[i]; end
        2'd1: begin v = e1_out[i]; d = e1_dir[i]; end
        2'd2: begin v = g2_out[0]; d = 1'b1; end
        default: begin v = g2_out[1]; d = 1'b1; end
      endcase
      v = v ^ cfg[i][4];
      case (cfg[i][3:2])
        2'd0: begin drv_oe[i] = 1'b0; drv_val[i] = 1'b0; end
        2'd1: begin drv_oe[i] = d; drv_val[i] = v; end
        2'd2: begin drv_oe[i] = ~v; drv_val[i] = 1'b0; end
        default: begin drv_oe[i] = 1'b1; drv_val[i] = v; end
      endcase
    end
    uio_out = drv_val[7:0];
    uio_oe  = drv_oe[7:0];
    // Output-only pins cannot release: open-drain acts as push-pull and a
    // released push-pull pin drives 0.
    for (i = 10; i < 13; i = i + 1) begin
      if (cfg[i][3:2] == 2'd2) uo_pins[i-10] = ~drv_oe[i];
      else uo_pins[i-10] = drv_oe[i] & drv_val[i];
    end
  end

  assign pins_in = {neg2, g2_out, uo_pins, in_val};

  // ------------------------------------------------------------------ read
  always @(*) begin
    bus_rdata = 8'h00;
    if (bus_addr[6:5] == 2'b01 && bus_addr[4:0] < 5'd13) bus_rdata = cfg[bus_addr[3:0]];
    else if (bus_addr == 7'h12) bus_rdata = {4'd0, negsel};
    else if (bus_addr == 7'h14) bus_rdata = pins_in[7:0];
    else if (bus_addr == 7'h15) bus_rdata = pins_in[15:8];
  end

endmodule
