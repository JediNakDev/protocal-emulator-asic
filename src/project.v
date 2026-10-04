/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Programmable protocol emulator. Pinout and behavior: docs/spec.md.
module tt_um_jedinakdev_protocol_emulator (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // Asynchronous assert, synchronous release.
  /* verilator lint_off SYNCASYNCNET */
  reg [1:0] rst_sync;
  /* verilator lint_on SYNCASYNCNET */
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) rst_sync <= 2'b00;
    else rst_sync <= {rst_sync[0], 1'b1};
  end
  wire rst = rst_sync[1];

  // ------------------------------------------------------------ host port
  wire [6:0] bus_addr;
  wire [7:0] bus_wdata, bus_lo_hold, bus_rdata;
  wire       bus_hi, bus_we, bus_commit, bus_fetch;
  wire [7:0] status_byte;
  wire       quad, hirq;
  wire [3:0] hdo;

  pe_host u_host (
      .clk        (clk),
      .rst_n      (rst),
      .hcs_n      (ui_in[0]),
      .hsck       (ui_in[1]),
      .hdi        (ui_in[5:2]),
      .hdo        (hdo),
      .quad_cfg   (quad),
      .status_byte(status_byte),
      .bus_addr   (bus_addr),
      .bus_wdata  (bus_wdata),
      .bus_lo_hold(bus_lo_hold),
      .bus_hi     (bus_hi),
      .bus_we     (bus_we),
      .bus_commit (bus_commit),
      .bus_fetch  (bus_fetch),
      .bus_rdata  (bus_rdata)
  );

  // ------------------------------------------------------- global registers
  wire [1:0]  enable, restart, step, fifo_clear;
  wire        div_sync;
  wire [2:0]  dbg_sel;
  wire        imem_we;
  wire [5:0]  imem_addr;
  wire [15:0] imem_wdata;
  wire [7:0]  flags;
  wire [7:0]  e0_fset, e0_fclr, e1_fset, e1_fclr;
  wire [31:0] counter;
  wire [1:0]  rx_nonempty, tx_notfull, stalled;
  wire [1:0]  st_rx_ovf, st_tx_ovf, st_rx_unf, st_cap_ovr;
  wire [7:0]  g_rdata;

  pe_global u_global (
      .clk        (clk),
      .rst_n      (rst),
      .bus_addr   (bus_addr),
      .bus_wdata  (bus_wdata),
      .bus_lo_hold(bus_lo_hold),
      .bus_hi     (bus_hi),
      .bus_we     (bus_we),
      .bus_fetch  (bus_fetch),
      .bus_rdata  (g_rdata),
      .enable     (enable),
      .restart    (restart),
      .step       (step),
      .fifo_clear (fifo_clear),
      .div_sync   (div_sync),
      .dbg_sel    (dbg_sel),
      .quad       (quad),
      .imem_we    (imem_we),
      .imem_addr  (imem_addr),
      .imem_wdata (imem_wdata),
      .flags      (flags),
      .flag_set   (e0_fset | e1_fset),
      .flag_clr   (e0_fclr | e1_fclr),
      .sticky_set ({st_cap_ovr, st_rx_unf, st_tx_ovf, st_rx_ovf}),
      .rx_nonempty(rx_nonempty),
      .tx_notfull (tx_notfull),
      .stalled    (stalled),
      .status_byte(status_byte),
      .hirq       (hirq),
      .counter    (counter)
  );

  // ----------------------------------------------------- instruction memory
  wire [5:0]  pc0, pc1;
  wire [15:0] instr0, instr1;

  pe_imem u_imem (
      .clk   (clk),
      .rst_n (rst),
      .we    (imem_we),
      .waddr (imem_addr),
      .wdata (imem_wdata),
      .raddr0(pc0),
      .rdata0(instr0),
      .raddr1(pc1),
      .rdata1(instr1)
  );

  // ---------------------------------------------------------------- pins
  wire [15:0] pins_in;
  wire [12:0] e0_out, e0_dir, e1_out, e1_dir;
  wire [1:0]  g2_out;
  wire [2:0]  uo_pins;
  wire [7:0]  p_rdata;

  pe_pins u_pins (
      .clk      (clk),
      .rst_n    (rst),
      .bus_addr (bus_addr),
      .bus_wdata(bus_wdata),
      .bus_we   (bus_we),
      .bus_rdata(p_rdata),
      .uio_in   (uio_in),
      .ui_pins  (ui_in[7:6]),
      .uio_out  (uio_out),
      .uio_oe   (uio_oe),
      .uo_pins  (uo_pins),
      .e0_out   (e0_out),
      .e0_dir   (e0_dir),
      .e1_out   (e1_out),
      .e1_dir   (e1_dir),
      .g2_out   (g2_out),
      .pins_in  (pins_in)
  );

  // --------------------------------------------------- bit state machine
  wire       g2_owner, g2_feed_full, g2_emit_bit;
  wire [1:0] g2_emit, div_tick, feed_wr, feed_bit;
  wire [7:0] g2_rdata;

  pe_g2 u_g2 (
      .clk      (clk),
      .rst_n    (rst),
      .bus_addr (bus_addr),
      .bus_wdata(bus_wdata),
      .bus_we   (bus_we),
      .bus_rdata(g2_rdata),
      .pins_in  (pins_in),
      .tick     (div_tick),
      .feed_wr  (feed_wr),
      .feed_din (feed_bit),
      .owner    (g2_owner),
      .feed_full(g2_feed_full),
      .out      (g2_out),
      .emit     (g2_emit),
      .emit_bit (g2_emit_bit)
  );

  // -------------------------------------------------------------- engines
  wire [7:0] e0_rdata, e1_rdata;

  pe_engine #(
      .BLOCK(2'b10)
  ) u_e0 (
      .clk           (clk),
      .rst_n         (rst),
      .bus_addr      (bus_addr),
      .bus_wdata     (bus_wdata),
      .bus_lo_hold   (bus_lo_hold),
      .bus_hi        (bus_hi),
      .bus_we        (bus_we),
      .bus_commit    (bus_commit),
      .bus_rdata     (e0_rdata),
      .enable        (enable[0]),
      .restart       (restart[0]),
      .step          (step[0]),
      .fifo_clear    (fifo_clear[0]),
      .div_sync      (div_sync),
      .dbg_sel       (dbg_sel),
      .pc_o          (pc0),
      .imem_data     (instr0),
      .pins_in       (pins_in),
      .pin_out       (e0_out),
      .pin_dir       (e0_dir),
      .flags         (flags),
      .flag_set      (e0_fset),
      .flag_clr      (e0_fclr),
      .counter       (counter),
      .g2_owner      (~g2_owner),
      .g2_feed_full  (g2_feed_full),
      .g2_feed_wr    (feed_wr[0]),
      .g2_feed_bit   (feed_bit[0]),
      .g2_emit       (g2_emit[0]),
      .g2_emit_bit   (g2_emit_bit),
      .div_tick      (div_tick[0]),
      .stalled       (stalled[0]),
      .rx_nonempty   (rx_nonempty[0]),
      .tx_notfull    (tx_notfull[0]),
      .st_rx_ovf     (st_rx_ovf[0]),
      .st_host_tx_ovf(st_tx_ovf[0]),
      .st_host_rx_unf(st_rx_unf[0]),
      .st_cap_ovr    (st_cap_ovr[0])
  );

  pe_engine #(
      .BLOCK(2'b11)
  ) u_e1 (
      .clk           (clk),
      .rst_n         (rst),
      .bus_addr      (bus_addr),
      .bus_wdata     (bus_wdata),
      .bus_lo_hold   (bus_lo_hold),
      .bus_hi        (bus_hi),
      .bus_we        (bus_we),
      .bus_commit    (bus_commit),
      .bus_rdata     (e1_rdata),
      .enable        (enable[1]),
      .restart       (restart[1]),
      .step          (step[1]),
      .fifo_clear    (fifo_clear[1]),
      .div_sync      (div_sync),
      .dbg_sel       (dbg_sel),
      .pc_o          (pc1),
      .imem_data     (instr1),
      .pins_in       (pins_in),
      .pin_out       (e1_out),
      .pin_dir       (e1_dir),
      .flags         (flags),
      .flag_set      (e1_fset),
      .flag_clr      (e1_fclr),
      .counter       (counter),
      .g2_owner      (g2_owner),
      .g2_feed_full  (g2_feed_full),
      .g2_feed_wr    (feed_wr[1]),
      .g2_feed_bit   (feed_bit[1]),
      .g2_emit       (g2_emit[1]),
      .g2_emit_bit   (g2_emit_bit),
      .div_tick      (div_tick[1]),
      .stalled       (stalled[1]),
      .rx_nonempty   (rx_nonempty[1]),
      .tx_notfull    (tx_notfull[1]),
      .st_rx_ovf     (st_rx_ovf[1]),
      .st_host_tx_ovf(st_tx_ovf[1]),
      .st_host_rx_unf(st_rx_unf[1]),
      .st_cap_ovr    (st_cap_ovr[1])
  );

  assign bus_rdata = g_rdata | p_rdata | g2_rdata | e0_rdata | e1_rdata;

  assign uo_out = {uo_pins, hirq, hdo};

  wire _unused = &{ena, 1'b0};

endmodule
