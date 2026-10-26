/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Pin block: input synchronizers, glitch filters and falling-edge sampling
// (G6), per-pin owner and drive mode, the falling-edge output phase, and the
// 16-entry pin space.
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

  // Arrays with a reset are registers, not memories (mem2reg says so).
  (* mem2reg *) reg [7:0] cfg [0:12];
  reg [3:0] negsel;
  reg [3:0] nsrc;    // NEGOUT: pin whose value is sampled on the falling edge
  reg [12:0] nmask;  // NEGMASK: pins whose value is XORed with that sample

  wire wr = bus_we & (bus_addr[6:5] == 2'b01) & (bus_addr[4:0] < 5'd13);

  always @(posedge clk or negedge rst_n) begin : config_regs
    integer i;
    if (!rst_n) begin
      for (i = 0; i < 13; i = i + 1) cfg[i] <= 8'h00;
      negsel <= 4'd0;
      nsrc   <= 4'd0;
      nmask  <= 13'd0;
    end else begin
      if (wr) cfg[bus_addr[3:0]] <= bus_wdata;
      if (bus_we && bus_addr == 7'h12) negsel <= bus_wdata[3:0];
      if (bus_we && bus_addr == 7'h1A) nsrc <= bus_wdata[3:0];
      if (bus_we && bus_addr == 7'h1B) nmask[7:0] <= bus_wdata;
      if (bus_we && bus_addr == 7'h1C) nmask[12:8] <= bus_wdata[4:0];
    end
  end

  // ---------------------------------------------------------------- inputs
  wire [9:0] raw = {ui_pins, uio_in};
  reg  [9:0] sync1, sync2;
  reg  [9:0] filt;
  (* mem2reg *) reg [2:0] fcnt [0:9];
  reg  [9:0] path;      // after the synchronizer or bypass
  reg  [9:0] in_val;    // after the filter

  always @(*) begin : input_path
    integer i;
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

  always @(posedge clk or negedge rst_n) begin : input_regs
    integer i;
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
  // Owner value after the invert bit, before the falling-edge phase.
  reg [12:0] own_val;
  reg [12:0] own_dir;
  reg        v, d;

  always @(*) begin : owner_mux
    integer i;
    for (i = 0; i < 13; i = i + 1) begin
      case (cfg[i][1:0])
        2'd0: begin v = e0_out[i]; d = e0_dir[i]; end
        2'd1: begin v = e1_out[i]; d = e1_dir[i]; end
        2'd2: begin v = g2_out[0]; d = 1'b1; end
        default: begin v = g2_out[1]; d = 1'b1; end
      endcase
      own_val[i] = v ^ cfg[i][4];
      own_dir[i] = d;
    end
  end

  // Falling-edge output phase: pin NEGOUT's value is sampled on the falling
  // edge, and pins in NEGMASK drive their value XORed with that sample, so a
  // change of the NEGOUT pin reaches them half a cycle after a rising edge.
  // `nfall` changes only on falling edges and feeds only the pads; the pin
  // space reads `nrise`, its copy at the rising edge, so readback stays a
  // full-cycle path: pins 10-12 read the value driven in the first half of
  // the cycle.
  reg nfall, nrise;
  always @(negedge clk or negedge rst_n) begin
    if (!rst_n) nfall <= 1'b0;
    else nfall <= (nsrc < 4'd13) ? own_val[nsrc] : 1'b0;
  end
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) nrise <= 1'b0;
    else nrise <= nfall;
  end

  // Drive stage, from the value after the falling-edge phase.
  function [1:0] drive;  // {oe, value}
    input [1:0] mode;
    input       val;
    input       dir;
    begin
      case (mode)
        2'd0: drive = 2'b00;
        2'd1: drive = {dir, val};
        2'd2: drive = {~val, 1'b0};
        default: drive = {1'b1, val};
      endcase
    end
  endfunction

  // An output-only pin cannot release: open-drain acts as push-pull and a
  // released push-pull pin drives 0.
  function uo_level;
    input [1:0] mode;
    input       val;
    input       dir;
    reg   [1:0] dv;
    begin
      dv = drive(mode, val, dir);
      uo_level = (mode == 2'd2) ? val : (dv[1] & dv[0]);
    end
  endfunction

  wire [12:0] pad_val  = own_val ^ (nmask & {13{nfall}});
  wire [12:0] read_val = own_val ^ (nmask & {13{nrise}});
  reg  [2:0]  uo_read;

  always @(*) begin : output_path
    integer i;
    reg [1:0] dv;
    for (i = 0; i < 8; i = i + 1) begin
      dv = drive(cfg[i][3:2], pad_val[i], own_dir[i]);
      uio_oe[i]  = dv[1];
      uio_out[i] = dv[0];
    end
    for (i = 10; i < 13; i = i + 1) begin
      uo_pins[i-10] = uo_level(cfg[i][3:2], pad_val[i], own_dir[i]);
      uo_read[i-10] = uo_level(cfg[i][3:2], read_val[i], own_dir[i]);
    end
  end

  assign pins_in = {neg2, g2_out, uo_read, in_val};

  // ------------------------------------------------------------------ read
  always @(*) begin
    bus_rdata = 8'h00;
    if (bus_addr[6:5] == 2'b01 && bus_addr[4:0] < 5'd13) bus_rdata = cfg[bus_addr[3:0]];
    else if (bus_addr == 7'h12) bus_rdata = {4'd0, negsel};
    else if (bus_addr == 7'h1A) bus_rdata = {4'd0, nsrc};
    else if (bus_addr == 7'h1B) bus_rdata = nmask[7:0];
    else if (bus_addr == 7'h1C) bus_rdata = {3'd0, nmask[12:8]};
    else if (bus_addr == 7'h14) bus_rdata = pins_in[7:0];
    else if (bus_addr == 7'h15) bus_rdata = pins_in[15:8];
  end

endmodule
