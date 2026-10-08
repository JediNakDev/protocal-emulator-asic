/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Global registers: control, coordination flags, sticky errors, host config,
// instruction memory write port, debug select and the cycle counter (G3).
module pe_global (
    input  wire        clk,
    input  wire        rst_n,
    // Host register bus
    input  wire [6:0]  bus_addr,
    input  wire [7:0]  bus_wdata,
    input  wire [7:0]  bus_lo_hold,
    input  wire        bus_hi,
    input  wire        bus_we,
    input  wire        bus_fetch,
    output reg  [7:0]  bus_rdata,
    // Engine control
    output reg  [1:0]  enable,
    output reg  [1:0]  restart,
    output reg  [1:0]  step,
    output reg  [1:0]  fifo_clear,
    output reg         div_sync,
    output reg  [2:0]  dbg_sel,
    output reg         quad,
    // Instruction memory write port
    output wire        imem_we,
    output reg  [5:0]  imem_addr,
    output wire [15:0] imem_wdata,
    // Flags
    output reg  [7:0]  flags,
    input  wire [7:0]  flag_set,
    input  wire [7:0]  flag_clr,
    // Sticky error events
    input  wire [7:0]  sticky_set,
    // Status inputs
    input  wire [1:0]  rx_nonempty,
    input  wire [1:0]  tx_notfull,
    input  wire [1:0]  stalled,
    output wire [7:0]  status_byte,
    output reg         hirq,
    output reg  [31:0] counter
);

  localparam [7:0] ID = 8'h50, VERSION = 8'h01;

  wire wr = bus_we & (bus_addr[6:5] == 2'b00);

  reg [7:0]  irq_mask;
  reg [7:0]  sticky;
  reg [7:0]  sticky_mask;
  reg [23:0] counter_snap;

  // CMD pulses come from registers, one cycle after the write, so no Host
  // bus decoding reaches the engines' issue logic in the same cycle.
  wire       cmd_wr = wr & (bus_addr[4:0] == 5'h03);

  assign imem_we    = wr & bus_hi & (bus_addr[4:0] == 5'h0B);
  assign imem_wdata = {bus_wdata, bus_lo_hold};

  wire [7:0] flag_wclr = (wr && bus_addr[4:0] == 5'h04) ? bus_wdata : 8'h00;
  wire [7:0] flag_wset = (wr && bus_addr[4:0] == 5'h05) ? bus_wdata : 8'h00;
  wire [7:0] stk_wclr  = (wr && bus_addr[4:0] == 5'h07) ? bus_wdata : 8'h00;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      enable       <= 2'b00;
      restart      <= 2'b00;
      step         <= 2'b00;
      fifo_clear   <= 2'b00;
      div_sync     <= 1'b0;
      dbg_sel      <= 3'd0;
      quad         <= 1'b0;
      imem_addr    <= 6'd0;
      flags        <= 8'h00;
      irq_mask     <= 8'h00;
      sticky       <= 8'h00;
      sticky_mask  <= 8'h00;
      hirq         <= 1'b0;
      counter      <= 32'd0;
      counter_snap <= 24'd0;
    end else begin
      counter <= counter + 32'd1;
      restart    <= cmd_wr ? bus_wdata[1:0] : 2'b00;
      step       <= cmd_wr ? bus_wdata[3:2] : 2'b00;
      fifo_clear <= cmd_wr ? bus_wdata[5:4] : 2'b00;
      div_sync   <= cmd_wr & bus_wdata[6];
      if (bus_fetch && bus_addr == 7'h16) counter_snap <= counter[31:8];

      // A set wins over a clear in the same cycle.
      flags  <= (flags & ~(flag_clr | flag_wclr)) | flag_set | flag_wset;
      sticky <= (sticky & ~stk_wclr) | sticky_set;
      hirq   <= |(flags & irq_mask) | |(sticky & sticky_mask);

      if (imem_we) imem_addr <= imem_addr + 6'd1;

      if (wr) begin
        case (bus_addr[4:0])
          5'h02: enable <= bus_wdata[1:0];
          5'h06: irq_mask <= bus_wdata;
          5'h08: sticky_mask <= bus_wdata;
          5'h09: quad <= bus_wdata[0];
          5'h0A: imem_addr <= bus_wdata[5:0];
          5'h13: dbg_sel <= bus_wdata[2:0];
          default: ;
        endcase
      end
    end
  end

  assign status_byte = {
    |sticky, hirq, stalled[1], stalled[0], tx_notfull[1], rx_nonempty[1], tx_notfull[0], rx_nonempty[0]
  };

  always @(*) begin
    bus_rdata = 8'h00;
    if (bus_addr[6:5] == 2'b00) begin
      case (bus_addr[4:0])
        5'h00: bus_rdata = ID;
        5'h01: bus_rdata = VERSION;
        5'h02: bus_rdata = {6'd0, enable};
        5'h04: bus_rdata = flags;
        5'h06: bus_rdata = irq_mask;
        5'h07: bus_rdata = sticky;
        5'h08: bus_rdata = sticky_mask;
        5'h09: bus_rdata = {7'd0, quad};
        5'h0A: bus_rdata = {2'd0, imem_addr};
        5'h13: bus_rdata = {5'd0, dbg_sel};
        5'h16: bus_rdata = counter[7:0];
        5'h17: bus_rdata = counter_snap[7:0];
        5'h18: bus_rdata = counter_snap[15:8];
        5'h19: bus_rdata = counter_snap[23:16];
        default: ;
      endcase
    end
  end

endmodule
