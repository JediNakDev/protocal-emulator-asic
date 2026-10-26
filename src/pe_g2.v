/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Bit state machine (G2): a 64 x 8 table indexed by {state, in1, in0}.
// Step source and input sources are selectable (H1). See docs/spec.md.
module pe_g2 (
    input  wire        clk,
    input  wire        rst_n,
    // Host register bus
    input  wire [6:0]  bus_addr,
    input  wire [7:0]  bus_wdata,
    input  wire        bus_we,
    output reg  [7:0]  bus_rdata,
    // Inputs
    input  wire [15:0] pins_in,
    input  wire [1:0]  tick,      // engine divider ticks
    input  wire [1:0]  feed_wr,   // engine `out g2` writes
    input  wire [1:0]  feed_din,
    // Clock recovery (G7)
    input  wire        cr_strobe,
    input  wire        cr_data,
    input  wire        cr_locked,
    // Outputs
    output reg         owner,
    output reg         feed_full,
    output reg  [1:0]  out,
    output wire [1:0]  emit,      // one-hot per engine
    output reg         emit_bit
);

  reg       en;
  reg [1:0] mode;
  reg       emit_en;
  reg       emit_other;  // deliver to the engine that does not own G2
  reg [4:0] in0_sel, in1_sel;
  reg [3:0] state;
  reg       feed_bit;
  reg [5:0] taddr;
  reg       emit_v;

  wire g2_wr = bus_we & (bus_addr[6:5] == 2'b00);

  wire tbl_we = g2_wr & (bus_addr[4:0] == 5'h0D);
  wire [64*8-1:0] tbl;

  pe_store #(
      .W (8),
      .D (64),
      .AW(6)
  ) u_table (
      .clk  (clk),
      .rst_n(rst_n),
      .we   (tbl_we),
      .waddr(taddr),
      .wdata(bus_wdata),
      .q    (tbl)
  );

  // Sources: 0-15 the pin space, 16 feed bit, 17 feed full, 18 recovered
  // data, 19 recovery locked, 20 recovery strobe, others 0.
  wire [4:0] src_ext = {cr_strobe, cr_locked, cr_data, feed_full, feed_bit};

  function sel_bit;
    input [4:0]  sel;
    input [15:0] pins;
    input [4:0]  ext;
    begin
      if (!sel[4]) sel_bit = pins[sel[3:0]];
      else if (sel[3:0] < 4'd5) sel_bit = ext[sel[2:0]];
      else sel_bit = 1'b0;
    end
  endfunction

  wire in0 = sel_bit(in0_sel, pins_in, src_ext);
  wire in1 = sel_bit(in1_sel, pins_in, src_ext);

  wire [7:0] entry = tbl[{state, in1, in0}*8 +: 8];

  wire owner_tick = owner ? tick[1] : tick[0];
  reg  step;
  always @(*) begin
    case (mode)
      2'd0: step = en;
      2'd1: step = en & owner_tick;
      2'd2: step = en & feed_full;
      default: step = en & cr_strobe;
    endcase
  end

  wire consume  = step & feed_full & ((mode == 2'd2) | entry[7]);
  wire owner_wr = owner ? feed_wr[1] : feed_wr[0];
  wire owner_d  = owner ? feed_din[1] : feed_din[0];
  wire state_wr = g2_wr & (bus_addr[4:0] == 5'h11);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      en        <= 1'b0;
      owner     <= 1'b0;
      mode      <= 2'd0;
      emit_en   <= 1'b0;
      emit_other <= 1'b0;
      in0_sel   <= 5'd0;
      in1_sel   <= 5'd0;
      state     <= 4'd0;
      out       <= 2'b00;
      feed_full <= 1'b0;
      feed_bit  <= 1'b0;
      taddr     <= 6'd0;
      emit_v    <= 1'b0;
      emit_bit  <= 1'b0;
    end else begin
      emit_v <= step & entry[6] & emit_en;
      if (step) begin
        state    <= entry[3:0];
        out      <= entry[5:4];
        emit_bit <= entry[4];
      end

      // A full feed refuses writes, so a write and a consume never coincide.
      if (consume) feed_full <= 1'b0;
      else if (owner_wr && !feed_full) begin
        feed_full <= 1'b1;
        feed_bit  <= owner_d;
      end

      if (tbl_we) taddr <= taddr + 6'd1;
      if (g2_wr) begin
        case (bus_addr[4:0])
          5'h0C: taddr <= bus_wdata[5:0];
          5'h0E: begin
            en      <= bus_wdata[0];
            owner   <= bus_wdata[1];
            mode    <= bus_wdata[3:2];
            emit_en <= bus_wdata[4];
            emit_other <= bus_wdata[5];
          end
          5'h0F: in0_sel <= bus_wdata[4:0];
          5'h10: in1_sel <= bus_wdata[4:0];
          5'h11: begin
            state     <= bus_wdata[3:0];
            out       <= bus_wdata[5:4];
            feed_full <= 1'b0;
          end
          default: ;
        endcase
      end
      if (state_wr) emit_v <= 1'b0;
    end
  end

  wire emit_to = owner ^ emit_other;
  assign emit = {emit_v & emit_to, emit_v & ~emit_to};

  always @(*) begin
    bus_rdata = 8'h00;
    if (bus_addr[6:5] == 2'b00) begin
      case (bus_addr[4:0])
        5'h0C: bus_rdata = {2'd0, taddr};
        5'h0E: bus_rdata = {2'd0, emit_other, emit_en, mode, owner, en};
        5'h0F: bus_rdata = {3'd0, in0_sel};
        5'h10: bus_rdata = {3'd0, in1_sel};
        5'h11: bus_rdata = {1'b0, feed_full, out, state};
        default: ;
      endcase
    end
  end

endmodule
