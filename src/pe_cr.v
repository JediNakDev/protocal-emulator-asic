/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Clock recovery (G7): a digital phase-locked loop that recovers a bit clock
// from the edges on one pin and strobes once per bit at a programmable
// phase. Its outputs feed the bit state machine. Behavior: docs/spec.md.
module pe_cr (
    input  wire        clk,
    input  wire        rst_n,
    // Host register bus
    input  wire [6:0]  bus_addr,
    input  wire [7:0]  bus_wdata,
    input  wire        bus_we,
    output reg  [7:0]  bus_rdata,
    // Pin space
    input  wire [15:0] pins_in,
    // Outputs, registered: one cycle after the strobed sample
    output reg         strobe,
    output reg         data,
    output reg         locked
);

  reg        en, dbl;
  reg [1:0]  lock_edge;
  reg [3:0]  pin, shift, loss;
  reg [15:0] inc;
  reg [7:0]  win, samp;

  // Phase of this cycle's rising-edge sample, in 1/65536 of a bit.
  reg [15:0] phase;
  reg        prev;   // the last sample of the previous cycle
  reg        armed;  // the last sample was in the half of the bit away from the sample point
  reg        have;   // an edge is selected for the next strobe
  reg [15:0] best_e; // its phase error
  reg        best_v; // the level after it
  reg [3:0]  miss;   // consecutive strobes without a selected edge
  reg [15:0] nedge;  // selected edges since locking, the locking edge included

  wire wr = bus_we & (bus_addr[6:4] == 3'b011);  // 0x30-0x3F

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en        <= 1'b0;
      dbl       <= 1'b0;
      lock_edge <= 2'd0;
      pin       <= 4'd0;
      shift     <= 4'd0;
      loss      <= 4'd0;
      inc       <= 16'd0;
      win       <= 8'd0;
      samp      <= 8'd0;
    end else if (wr) begin
      case (bus_addr[3:0])
        4'h0: begin
          en        <= bus_wdata[0];
          dbl       <= bus_wdata[1];
          lock_edge <= bus_wdata[3:2];
          pin       <= bus_wdata[7:4];
        end
        4'h1: begin
          shift <= bus_wdata[3:0];
          loss  <= bus_wdata[7:4];
        end
        4'h2: inc[7:0] <= bus_wdata;
        4'h3: inc[15:8] <= bus_wdata;
        4'h4: win <= bus_wdata;
        4'h5: samp <= bus_wdata;
        default: ;
      endcase
    end
  end

  // ------------------------------------------------------------- samples
  // s0 at `phase`; in double rate also s1, pin 15, at `phase + inc`.
  wire        s0 = pins_in[pin];
  wire        s1 = pins_in[15];
  wire [15:0] half = {1'b0, inc[15:1]};

  // An edge lies midway between the two samples that differ. Its error is
  // its phase as a signed number: the distance from phase 0.
  wire        edge0 = (prev != s0);
  wire        edge1 = dbl & (s0 != s1);
  wire [15:0] e0 = phase - half;
  wire [15:0] e1 = phase + half;

  // Distance from phase zero in 1/256 of a bit, from the error's top byte:
  // an edge is in the window if its distance is below WIN.
  function [6:0] edist;
    input [7:0] e_hi;
    begin
      edist = e_hi[7] ? ~e_hi[6:0] : e_hi[6:0];
    end
  endfunction

  wire c0 = edge0 & ({1'b0, edist(e0[15:8])} < win);
  wire c1 = edge1 & ({1'b0, edist(e1[15:8])} < win);

  // Lock edge polarity: 0 rising, 1 falling, 2 or 3 either.
  wire l0 = edge0 & (lock_edge[1] | (s0 ^ lock_edge[0]));
  wire l1 = edge1 & (lock_edge[1] | (s1 ^ lock_edge[0]));

  // -------------------------------------------------------------- strobe
  // A sample is far from the sample point when r = phase - S + INC/2 has its
  // top bit set; the unit strobes at the first sample that is not far after
  // one that was, which is the sample nearest S.
  wire [15:0] r0 = phase - {samp, 8'h00} + half;
  wire [15:0] r1 = r0 + inc;
  wire        far0 = r0[15];
  wire        far1 = r1[15];
  wire        fire0 = locked & armed & ~far0;
  wire        fire1 = locked & dbl & far0 & ~far1;
  wire        fire = fire0 | fire1;
  wire        _unused = &{1'b0, r1[14:0]};

  // ----------------------------------------------------------- selection
  // Of the edges in the window between two strobes, the one nearest phase
  // zero is selected; of equally near ones, the earlier. An edge before the
  // strobed sample belongs to that strobe, a later one to the next.
  wire        a0 = have & (~c0 | (edist(best_e[15:8]) <= edist(e0[15:8])));
  wire        h0 = have | c0;                 // selection up to sample 0
  wire [15:0] e_s0 = a0 ? best_e : e0;
  wire        v_s0 = a0 ? best_v : s0;
  wire        a1 = h0 & (~c1 | (edist(e_s0[15:8]) <= edist(e1[15:8])));
  wire        h1 = h0 | c1;                   // selection up to sample 1
  wire [15:0] e_s1 = a1 ? e_s0 : e1;
  wire        v_s1 = a1 ? v_s0 : s1;

  wire        sel   = fire1 ? h1 : h0;        // the strobe has an edge
  wire [15:0] sel_e = fire1 ? e_s1 : e_s0;
  wire        sel_v = fire1 ? v_s1 : v_s0;

  // ---------------------------------------------------------- correction
  // The n-th selected edge since locking shifts its error right by
  // min(K, log2(n)), so the phase starts as an average of the edges and
  // settles at gain 2^-K. Locking subtracts the whole error.
  function [3:0] log2_16;
    input [15:0] v;
    integer i;
    begin
      log2_16 = 4'd0;
      for (i = 1; i < 16; i = i + 1) if (v[i]) log2_16 = i[3:0];
    end
  endfunction

  wire        lock_hit = ~locked & (l0 | l1);
  wire [15:0] lock_e = l1 ? e1 : e0;
  wire        track = fire & sel;
  wire [15:0] nedge_inc = (nedge == 16'hFFFF) ? nedge : nedge + 16'd1;
  wire [3:0]  gear_log = log2_16(nedge_inc);
  wire [3:0]  gear = (gear_log < shift) ? gear_log : shift;
  wire [15:0] track_corr = $unsigned($signed(sel_e) >>> gear);
  wire [15:0] corr = lock_hit ? lock_e : (track ? track_corr : 16'd0);
  wire [15:0] step_inc = dbl ? {inc[14:0], 1'b0} : inc;

  wire lose = fire & ~sel & (miss >= loss) & (loss != 4'd15);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      phase  <= 16'd0;
      prev   <= 1'b0;
      armed  <= 1'b0;
      have   <= 1'b0;
      best_e <= 16'd0;
      best_v <= 1'b0;
      miss   <= 4'd0;
      nedge  <= 16'd0;
      strobe <= 1'b0;
      data   <= 1'b0;
      locked <= 1'b0;
    end else if (!en) begin
      phase  <= 16'd0;
      prev   <= dbl ? s1 : s0;
      armed  <= 1'b0;
      have   <= 1'b0;
      best_e <= 16'd0;
      best_v <= 1'b0;
      miss   <= 4'd0;
      nedge  <= 16'd0;
      strobe <= 1'b0;
      data   <= 1'b0;
      locked <= 1'b0;
    end else begin
      phase  <= phase + step_inc - corr;
      prev   <= dbl ? s1 : s0;
      strobe <= fire;
      if (lock_hit) begin
        // The locking edge is selected for the first strobe, at error 0.
        locked <= 1'b1;
        armed  <= 1'b1;
        have   <= 1'b1;
        best_e <= 16'd0;
        best_v <= l1 ? s1 : s0;
        miss   <= 4'd0;
        nedge  <= 16'd0;
      end else if (locked) begin
        armed <= dbl ? far1 : far0;
        if (fire) begin
          data <= sel ? sel_v : (fire1 ? s1 : s0);
          if (sel) begin
            nedge <= nedge_inc;
            miss  <= 4'd0;
          end else if (miss != 4'd15) begin
            miss <= miss + 4'd1;
          end
          if (lose) locked <= 1'b0;
          // After a strobe on sample 0, an edge before sample 1 starts the
          // next selection, its error moved by this strobe's correction.
          have   <= fire0 & c1;
          best_e <= e1 - corr;
          best_v <= s1;
        end else begin
          have   <= h1;
          best_e <= e_s1;
          best_v <= v_s1;
        end
      end
    end
  end

  always @(*) begin
    bus_rdata = 8'h00;
    if (bus_addr[6:4] == 3'b011) begin
      case (bus_addr[3:0])
        4'h0: bus_rdata = {pin, lock_edge, dbl, en};
        4'h1: bus_rdata = {loss, shift};
        4'h2: bus_rdata = inc[7:0];
        4'h3: bus_rdata = inc[15:8];
        4'h4: bus_rdata = win;
        4'h5: bus_rdata = samp;
        4'h6: bus_rdata = {miss, 2'b00, data, locked};
        default: ;
      endcase
    end
  end

endmodule
