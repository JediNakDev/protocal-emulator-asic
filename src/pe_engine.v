/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// One protocol engine: configuration registers, execution unit, buffers
// (G5), checksum shift register (G1) and edge capture (G3).
// Instruction set, timing and register map: docs/spec.md.
module pe_engine #(
    parameter [1:0] BLOCK = 2'b10  // bus_addr[6:5] of this engine's registers
) (
    input  wire        clk,
    input  wire        rst_n,
    // Host register bus
    input  wire [6:0]  bus_addr,
    input  wire [7:0]  bus_wdata,
    input  wire [7:0]  bus_lo_hold,
    input  wire        bus_hi,
    input  wire        bus_we,
    input  wire        bus_commit,
    input  wire        bus_fetch,
    output reg  [7:0]  bus_rdata,
    // Global control
    input  wire        enable,
    input  wire        restart,
    input  wire        step,
    input  wire        fifo_clear,
    input  wire        div_sync,
    input  wire [2:0]  dbg_sel,
    // Instruction memory
    output wire [5:0]  pc_o,
    input  wire [15:0] imem_data,
    // Pins
    input  wire [15:0] pins_in,
    output reg  [12:0] pin_out,
    output reg  [12:0] pin_dir,
    // Coordination flags
    input  wire [7:0]  flags,
    output wire [7:0]  flag_set,
    output reg  [7:0]  flag_clr,
    // Global counter
    input  wire [31:0] counter,
    // Bit state machine (G2)
    input  wire        g2_owner,
    input  wire        g2_feed_full,
    output reg         g2_feed_wr,
    output reg         g2_feed_bit,
    input  wire        g2_emit,
    input  wire        g2_emit_bit,
    output wire        div_tick,
    // Status
    output reg         stalled,
    output wire        rx_nonempty,
    output wire        tx_notfull,
    output wire        st_rx_ovf,
    output wire        st_host_tx_ovf,
    output wire        st_host_rx_unf,
    output reg         st_cap_ovr
);

  localparam [4:0] SIXTEEN = 5'd16;

  localparam [2:0] OP_WAIT = 3'd2, OP_IN = 3'd3, OP_OUT = 3'd4, OP_CTRL = 3'd5, OP_MOV = 3'd6,
      OP_SET = 3'd7;

  localparam [3:0] OUT_PINS = 4'd0, OUT_X = 4'd1, OUT_Y = 4'd2, OUT_PINDIRS = 4'd4, OUT_PC = 4'd5,
      OUT_ISR = 4'd6, OUT_EXEC = 4'd7, OUT_LFSR = 4'd8, OUT_G2 = 4'd9, OUT_RAM = 4'd10;

  // ======================================================== host register bus
  wire       hit = (bus_addr[6:5] == BLOCK);
  wire [4:0] off = bus_addr[4:0];
  wire       wr = bus_we & hit;

  reg [15:0] clkdiv;
  reg [5:0]  wrap_bot, wrap_top;
  reg        autopush, autopull, in_right, out_right;
  reg [2:0]  fifo_mode;
  reg [3:0]  push_thr_r, pull_thr_r;
  reg [3:0]  out_base, out_cnt_r, set_base, in_base, jmp_pin, side_base;
  reg [2:0]  set_cnt_r;
  reg [1:0]  side_cnt;
  reg        side_opt, side_dirs;
  reg [5:0]  jmp_base;
  reg        jmp_pat;
  reg [3:0]  status_n;
  reg        status_sel;
  reg [1:0]  lfsr_feed, lfsr_mode;
  reg        lfsr_right;
  reg [3:0]  cap_pin;
  reg [1:0]  cap_edge;
  reg        cap_flag_en;
  reg [2:0]  cap_flag;
  reg [2:0]  ram_addr;
  reg [31:0] lfsr_poly;
  reg [31:0] capture;

  wire [4:0] push_thr = (push_thr_r == 4'd0) ? SIXTEEN : {1'b0, push_thr_r};
  wire [4:0] pull_thr = (pull_thr_r == 4'd0) ? SIXTEEN : {1'b0, pull_thr_r};
  wire [4:0] out_cnt  = (out_cnt_r == 4'd0) ? SIXTEEN : {1'b0, out_cnt_r};
  wire [4:0] set_cnt  = (set_cnt_r > 3'd5) ? 5'd5 : {2'b00, set_cnt_r};

  wire forced_wr = wr & bus_hi & (off == 5'h0F);
  wire h_ram_we  = wr & bus_hi & (off == 5'h13);
  wire h_ram_adv = h_ram_we | (bus_commit & hit & bus_hi & (off == 5'h13));

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      clkdiv      <= 16'd0;
      wrap_bot    <= 6'd0;
      wrap_top    <= 6'd63;
      autopush    <= 1'b0;
      autopull    <= 1'b0;
      in_right    <= 1'b0;
      out_right   <= 1'b0;
      fifo_mode   <= 3'd0;
      push_thr_r  <= 4'd0;
      pull_thr_r  <= 4'd0;
      out_base    <= 4'd0;
      out_cnt_r   <= 4'd1;
      set_base    <= 4'd0;
      set_cnt_r   <= 3'd1;
      in_base     <= 4'd0;
      jmp_pin     <= 4'd0;
      side_base   <= 4'd0;
      side_cnt    <= 2'd0;
      side_opt    <= 1'b0;
      side_dirs   <= 1'b0;
      jmp_base    <= 6'd0;
      jmp_pat     <= 1'b0;
      status_n    <= 4'd0;
      status_sel  <= 1'b0;
      lfsr_feed   <= 2'd0;
      lfsr_mode   <= 2'd0;
      lfsr_right  <= 1'b0;
      cap_pin     <= 4'd0;
      cap_edge    <= 2'd0;
      cap_flag_en <= 1'b0;
      cap_flag    <= 3'd0;
      ram_addr    <= 3'd0;
      lfsr_poly   <= 32'd0;
    end else begin
      if (h_ram_adv) ram_addr <= ram_addr + 3'd1;
      if (wr) begin
        case (off)
          5'h00: clkdiv[7:0] <= bus_wdata;
          5'h01: clkdiv[15:8] <= bus_wdata;
          5'h02: wrap_bot <= bus_wdata[5:0];
          5'h03: wrap_top <= bus_wdata[5:0];
          5'h04: begin
            autopush  <= bus_wdata[0];
            autopull  <= bus_wdata[1];
            in_right  <= bus_wdata[2];
            out_right <= bus_wdata[3];
            fifo_mode <= bus_wdata[6:4];
          end
          5'h05: begin
            push_thr_r <= bus_wdata[3:0];
            pull_thr_r <= bus_wdata[7:4];
          end
          5'h06: begin
            out_base  <= bus_wdata[3:0];
            out_cnt_r <= bus_wdata[7:4];
          end
          5'h07: begin
            set_base  <= bus_wdata[3:0];
            set_cnt_r <= bus_wdata[6:4];
          end
          5'h08: begin
            in_base <= bus_wdata[3:0];
            jmp_pin <= bus_wdata[7:4];
          end
          5'h09: begin
            side_base <= bus_wdata[3:0];
            side_cnt  <= bus_wdata[5:4];
            side_opt  <= bus_wdata[6];
            side_dirs <= bus_wdata[7];
          end
          5'h0A: begin
            jmp_base <= bus_wdata[5:0];
            jmp_pat  <= bus_wdata[6];
          end
          5'h0B: begin
            status_n   <= bus_wdata[3:0];
            status_sel <= bus_wdata[4];
          end
          5'h0C: begin
            lfsr_feed  <= bus_wdata[1:0];
            lfsr_mode  <= bus_wdata[3:2];
            lfsr_right <= bus_wdata[4];
          end
          5'h0D: begin
            cap_pin     <= bus_wdata[3:0];
            cap_edge    <= bus_wdata[5:4];
            cap_flag_en <= bus_wdata[6];
          end
          5'h0E: cap_flag <= bus_wdata[2:0];
          5'h12: ram_addr <= bus_wdata[2:0];
          5'h14: lfsr_poly[7:0] <= bus_wdata;
          5'h15: lfsr_poly[15:8] <= bus_wdata;
          5'h16: lfsr_poly[23:16] <= bus_wdata;
          5'h17: lfsr_poly[31:24] <= bus_wdata;
          default: ;
        endcase
      end
    end
  end

  // ======================================================== execution state
  reg [5:0]  pc;
  reg [15:0] x, y, isr, osr, exec_instr;
  reg [4:0]  isr_cnt, osr_cnt, delay_cnt;
  reg [15:0] div_cnt;
  reg        exec_valid, irqw;
  reg [31:0] lfsr_val;

  // ================================================================ buffers
  wire [15:0] tx_data, rx_head, ram_rdata_e, ram_rdata_h;
  wire [3:0]  tx_level, rx_level;
  wire        tx_empty, tx_full, rx_empty, rx_full;
  reg         e_tx_pop, e_rx_push, e_ram_we;
  reg  [15:0] e_rx_data, e_ram_wdata;
  reg         rx_drop;

  // The Host holds the low byte; hold its matching high byte and empty state.
  // An empty read must not consume a word that arrives during the transfer.
  reg [7:0]  rx_read_hi;
  reg        rx_read_empty;
  wire       rx_read_commit = bus_commit & hit & bus_hi & (off == 5'h10);
  wire       h_rx_unf;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      rx_read_hi    <= 8'd0;
      rx_read_empty <= 1'b1;
    end else if (bus_fetch && hit && !bus_hi && off == 5'h10) begin
      rx_read_hi    <= rx_head[15:8];
      rx_read_empty <= rx_empty;
    end
  end

  pe_fifo u_fifo (
      .clk        (clk),
      .rst_n      (rst_n),
      .clear      (fifo_clear | (wr & (off == 5'h04))),
      .mode       (fifo_mode),
      .h_tx_push  (wr & bus_hi & (off == 5'h10)),
      .h_tx_data  ({bus_wdata, bus_lo_hold}),
      .h_rx_pop   (rx_read_commit & ~rx_read_empty),
      .h_rx_data  (rx_head),
      .h_ram_we   (h_ram_we),
      .h_ram_idx  (ram_addr),
      .h_ram_wdata({bus_wdata, bus_lo_hold}),
      .h_ram_rdata(ram_rdata_h),
      .h_tx_drop  (st_host_tx_ovf),
      .h_rx_unf   (h_rx_unf),
      .e_tx_pop   (e_tx_pop),
      .e_tx_data  (tx_data),
      .e_rx_push  (e_rx_push),
      .e_rx_data  (e_rx_data),
      .e_ram_we   (e_ram_we),
      .e_ram_idx  (y[2:0]),
      .e_ram_wdata(e_ram_wdata),
      .e_ram_rdata(ram_rdata_e),
      .tx_level   (tx_level),
      .rx_level   (rx_level),
      .tx_empty   (tx_empty),
      .tx_full    (tx_full),
      .rx_empty   (rx_empty),
      .rx_full    (rx_full)
  );

  assign rx_nonempty = ~rx_empty;
  assign tx_notfull  = ~tx_full;
  assign st_rx_ovf   = rx_drop;
  assign st_host_rx_unf = h_rx_unf | (rx_read_commit & rx_read_empty);

  assign pc_o = pc;

  wire [15:0] instr = exec_valid ? exec_instr : imem_data;
  wire [2:0]  op = instr[15:13];
  wire [3:0]  fld = instr[7:4];  // `in` source / `out` destination
  wire [4:0]  nbits = (instr[3:0] == 4'd0) ? SIXTEEN : {1'b0, instr[3:0]};
  wire        one_bit = (instr[3:0] == 4'd1);

  assign div_tick = enable & (div_cnt == clkdiv);
  wire tick  = enable ? div_tick : (step | exec_valid);
  wire issue = tick & (delay_cnt == 5'd0);

  // --------------------------------------------------- side-set and delay
  reg        side_valid;
  reg [2:0]  side_val;
  reg [4:0]  dly;
  wire [4:0] sd = instr[12:8];

  always @(*) begin
    side_valid = 1'b0;
    side_val   = 3'd0;
    dly        = sd;
    case ({side_opt, side_cnt})
      3'b001: begin side_valid = 1'b1; side_val = {2'b00, sd[4]}; dly = {1'b0, sd[3:0]}; end
      3'b010: begin side_valid = 1'b1; side_val = {1'b0, sd[4:3]}; dly = {2'b00, sd[2:0]}; end
      3'b011: begin side_valid = 1'b1; side_val = sd[4:2]; dly = {3'b000, sd[1:0]}; end
      3'b100: begin dly = {1'b0, sd[3:0]}; end
      3'b101: begin side_valid = sd[4]; side_val = {2'b00, sd[3]}; dly = {2'b00, sd[2:0]}; end
      3'b110: begin side_valid = sd[4]; side_val = {1'b0, sd[3:2]}; dly = {3'b000, sd[1:0]}; end
      3'b111: begin side_valid = sd[4]; side_val = sd[3:1]; dly = {4'b0000, sd[0]}; end
      default: ;
    endcase
  end

  // ------------------------------------------------------------- helpers
  // Write `cnt` bits of `data` to pins base, base+1, ... (modulo 16).
  function [12:0] wr_range;
    input [12:0] cur;
    input [3:0]  base;
    input [4:0]  cnt;
    input [15:0] data;
    integer k;
    reg [3:0] o;
    begin
      wr_range = cur;
      for (k = 0; k < 13; k = k + 1) begin
        o = k[3:0] - base;
        if ({1'b0, o} < cnt) wr_range[k] = data[o];
      end
    end
  endfunction

  function [4:0] sat16;
    input [5:0] v;
    begin
      sat16 = (v > 6'd16) ? SIXTEEN : v[4:0];
    end
  endfunction

  function [15:0] rev16;
    input [15:0] v;
    integer k;
    begin
      for (k = 0; k < 16; k = k + 1) rev16[k] = v[15-k];
    end
  endfunction

  wire [15:0] pins_rot = (pins_in >> in_base) | (pins_in << (5'd16 - {1'b0, in_base}));
  wire        pat_match = ((pins_in & y) == x);
  wire [15:0] mask_n = 16'hFFFF >> (5'd16 - nbits);
  wire [3:0]  st_level = status_sel ? rx_level : tx_level;
  wire [15:0] status_val = ({1'b0, st_level} < {1'b0, status_n}) ? 16'hFFFF : 16'h0000;

  // ------------------------------------------------------ source values
  reg [15:0] in_val;
  always @(*) begin
    case (fld)
      4'd0: in_val = pins_rot;
      4'd1: in_val = x;
      4'd2: in_val = y;
      4'd4: in_val = ram_rdata_e;
      4'd5: in_val = counter[15:0];
      4'd6: in_val = isr;
      4'd7: in_val = osr;
      4'd8: in_val = lfsr_val[15:0];
      4'd9: in_val = lfsr_val[31:16];
      4'd10: in_val = capture[15:0];
      4'd11: in_val = capture[31:16];
      default: in_val = 16'h0000;
    endcase
  end

  reg [15:0] mov_src, mov_val;
  always @(*) begin
    case (instr[2:0])
      3'd0: mov_src = pins_rot;
      3'd1: mov_src = x;
      3'd2: mov_src = y;
      3'd4: mov_src = status_val;
      3'd5: mov_src = isr;
      3'd6: mov_src = osr;
      3'd7: mov_src = ram_rdata_e;
      default: mov_src = 16'h0000;
    endcase
    case (instr[4:3])
      2'd1: mov_val = ~mov_src;
      2'd2: mov_val = rev16(mov_src);
      default: mov_val = mov_src;
    endcase
  end

  // --------------------------------------------------- OSR / ISR datapath
  wire        osr_empty = (osr_cnt >= pull_thr);
  wire        refill = autopull & osr_empty;
  wire [15:0] osr_src = refill ? tx_data : osr;
  wire [4:0]  osr_cnt_src = refill ? 5'd0 : osr_cnt;
  wire [15:0] out_raw = out_right ? (osr_src & mask_n) : (osr_src >> (5'd16 - nbits));
  wire [15:0] osr_after = out_right ? (osr_src >> nbits) : (osr_src << nbits);

  // Checksum register (G1): one step per cycle, from a G2 emission or from
  // a 1-bit `in` / `out` instruction.
  wire emit_lfsr = g2_emit & lfsr_feed[1];
  wire in_lfsr = (op == OP_IN) & one_bit & lfsr_feed[1];
  wire out_lfsr = (op == OP_OUT) & one_bit & lfsr_feed[0] & (fld != OUT_LFSR);
  wire lfsr_din = emit_lfsr ? g2_emit_bit : ((op == OP_IN) ? in_val[0] : out_raw[0]);
  wire lfsr_dout;
  wire [31:0] lfsr_next;

  pe_lfsr u_lfsr (
      .val  (lfsr_val),
      .poly (lfsr_poly),
      .mode (lfsr_mode),
      .right(lfsr_right),
      .din  (lfsr_din),
      .dout (lfsr_dout),
      .next (lfsr_next)
  );

  wire [15:0] out_data = out_lfsr ? {15'd0, lfsr_dout} : out_raw;
  wire [15:0] in_data = in_lfsr ? {in_val[15:1], lfsr_dout} : in_val;
  wire [15:0] isr_after_in = in_right ? ((isr >> nbits) | (in_data << (5'd16 - nbits)))
                                      : ((isr << nbits) | (in_data & mask_n));
  wire [4:0]  isr_cnt_in = sat16({1'b0, isr_cnt} + {1'b0, nbits});

  // An emission conflicts with instructions that modify the ISR or step the
  // checksum register; they stall for that cycle.
  wire touch_isr = (op == OP_IN) |
                   ((op == OP_CTRL) & (instr[7:6] == 2'b00)) |
                   ((op == OP_MOV) & (instr[7:5] == 3'd6)) |
                   ((op == OP_OUT) & (fld == OUT_ISR));
  wire touch_lfsr = in_lfsr | out_lfsr | ((op == OP_OUT) & (fld == OUT_LFSR));
  wire emit_conflict = g2_emit & (touch_isr | (touch_lfsr & lfsr_feed[1]));

  // ============================================================ next state
  reg [5:0]  n_pc;
  reg [15:0] n_x, n_y, n_isr, n_osr, n_exec_instr;
  reg [4:0]  n_isr_cnt, n_osr_cnt, n_delay;
  reg [12:0] n_pin_out, n_pin_dir;
  reg        n_exec_valid, n_irqw;
  reg [31:0] n_lfsr;
  reg        stall, jump, exec_new, cond, wcond;
  reg [5:0]  jtarget;
  reg        emit_b;
  reg [7:0]  i_flag_set;
  reg [15:0] isr_e;
  reg [4:0]  cnt_e;

  always @(*) begin
    n_pc         = pc;
    n_x          = x;
    n_y          = y;
    n_isr        = isr;
    n_isr_cnt    = isr_cnt;
    n_osr        = osr;
    n_osr_cnt    = osr_cnt;
    n_pin_out    = pin_out;
    n_pin_dir    = pin_dir;
    n_exec_valid = exec_valid;
    n_exec_instr = exec_instr;
    n_irqw       = irqw;
    n_delay      = delay_cnt;
    n_lfsr       = lfsr_val;
    stall        = 1'b0;
    jump         = 1'b0;
    jtarget      = 6'd0;
    exec_new     = 1'b0;
    cond         = 1'b0;
    wcond        = 1'b0;
    e_tx_pop     = 1'b0;
    e_rx_push    = 1'b0;
    e_rx_data    = 16'h0000;
    e_ram_we     = 1'b0;
    e_ram_wdata  = 16'h0000;
    i_flag_set   = 8'h00;
    flag_clr     = 8'h00;
    g2_feed_wr   = 1'b0;
    g2_feed_bit  = 1'b0;
    rx_drop      = 1'b0;
    emit_b       = 1'b0;
    isr_e        = 16'h0000;
    cnt_e        = 5'd0;

    if (tick && delay_cnt != 5'd0) n_delay = delay_cnt - 5'd1;

    if (issue) begin
      if (emit_conflict) begin
        stall = 1'b1;
      end else begin
        case (op)
          3'd0, 3'd1: begin  // jmp
            case ({instr[13], instr[7:6]})
              3'd0: cond = 1'b1;
              3'd1: cond = (x == 16'd0);
              3'd2: begin cond = (x != 16'd0); n_x = x - 16'd1; end
              3'd3: cond = (y == 16'd0);
              3'd4: begin cond = (y != 16'd0); n_y = y - 16'd1; end
              3'd5: cond = (x != y);
              3'd6: cond = jmp_pat ? pat_match : pins_in[jmp_pin];
              default: cond = ~osr_empty;
            endcase
            jump    = cond;
            jtarget = instr[5:0];
          end

          OP_WAIT: begin
            case (instr[6:5])
              2'd0: wcond = pins_in[instr[3:0]];
              2'd1: wcond = pins_in[in_base+instr[3:0]];
              2'd2: wcond = flags[instr[2:0]];
              default: wcond = pat_match;
            endcase
            if (wcond != instr[7]) stall = 1'b1;
            else if (instr[6:5] == 2'd2 && instr[7]) flag_clr[instr[2:0]] = 1'b1;
          end

          OP_IN: begin
            if (autopush && isr_cnt_in >= push_thr) begin
              if (rx_full) begin
                stall = 1'b1;
              end else begin
                e_rx_push = 1'b1;
                e_rx_data = isr_after_in;
                n_isr     = 16'h0000;
                n_isr_cnt = 5'd0;
              end
            end else begin
              n_isr     = isr_after_in;
              n_isr_cnt = isr_cnt_in;
            end
            if (!stall && in_lfsr) n_lfsr = lfsr_next;
          end

          OP_OUT: begin
            if (refill && tx_empty) begin
              stall = 1'b1;
            end else if (fld == OUT_G2 && g2_owner && g2_feed_full) begin
              stall = 1'b1;
            end else begin
              e_tx_pop  = refill;
              n_osr     = osr_after;
              n_osr_cnt = sat16({1'b0, osr_cnt_src} + {1'b0, nbits});
              if (out_lfsr) n_lfsr = lfsr_next;
              case (fld)
                OUT_PINS: n_pin_out = wr_range(pin_out, out_base, out_cnt, out_data);
                OUT_X: n_x = out_data;
                OUT_Y: n_y = out_data;
                OUT_PINDIRS: n_pin_dir = wr_range(pin_dir, out_base, out_cnt, out_data);
                OUT_PC: begin
                  jump    = 1'b1;
                  jtarget = jmp_base + out_data[5:0];
                end
                OUT_ISR: begin
                  n_isr     = out_data;
                  n_isr_cnt = nbits;
                end
                OUT_EXEC: begin
                  n_exec_instr = out_data;
                  exec_new     = 1'b1;
                end
                OUT_LFSR: n_lfsr = (lfsr_val << nbits) | {16'd0, out_data};
                OUT_G2: begin
                  g2_feed_wr  = g2_owner;
                  g2_feed_bit = out_data[0];
                end
                OUT_RAM: begin
                  e_ram_we    = 1'b1;
                  e_ram_wdata = out_data;
                end
                default: ;
              endcase
            end
          end

          OP_CTRL: begin
            case (instr[7:6])
              2'b00: begin  // push
                if (!instr[5] || isr_cnt >= push_thr) begin
                  if (rx_full) begin
                    if (instr[4]) begin
                      stall = 1'b1;
                    end else begin
                      rx_drop   = 1'b1;
                      n_isr     = 16'h0000;
                      n_isr_cnt = 5'd0;
                    end
                  end else begin
                    e_rx_push = 1'b1;
                    e_rx_data = isr;
                    n_isr     = 16'h0000;
                    n_isr_cnt = 5'd0;
                  end
                end
              end
              2'b01: begin  // pull
                if (!instr[5] || osr_empty) begin
                  if (tx_empty) begin
                    if (instr[4]) begin
                      stall = 1'b1;
                    end else begin
                      n_osr     = x;
                      n_osr_cnt = 5'd0;
                    end
                  end else begin
                    e_tx_pop  = 1'b1;
                    n_osr     = tx_data;
                    n_osr_cnt = 5'd0;
                  end
                end
              end
              2'b10: begin  // irq
                if (instr[5]) begin
                  flag_clr[instr[2:0]] = 1'b1;
                end else if (!irqw) begin
                  i_flag_set[instr[2:0]] = 1'b1;
                  if (instr[4]) begin
                    n_irqw = 1'b1;
                    stall  = 1'b1;
                  end
                end else if (flags[instr[2:0]]) begin
                  stall = 1'b1;
                end else begin
                  n_irqw = 1'b0;
                end
              end
              default: ;
            endcase
          end

          OP_MOV: begin
            case (instr[7:5])
              3'd0: n_pin_out = wr_range(pin_out, out_base, out_cnt, mov_val);
              3'd1: n_x = mov_val;
              3'd2: n_y = mov_val;
              3'd3: n_pin_dir = wr_range(pin_dir, out_base, out_cnt, mov_val);
              3'd4: begin
                n_exec_instr = mov_val;
                exec_new     = 1'b1;
              end
              3'd5: begin
                jump    = 1'b1;
                jtarget = mov_val[5:0];
              end
              3'd6: begin
                n_isr     = mov_val;
                n_isr_cnt = 5'd0;
              end
              default: begin
                n_osr     = mov_val;
                n_osr_cnt = 5'd0;
              end
            endcase
          end

          OP_SET: begin
            case (instr[7:5])
              3'd0: n_pin_out = wr_range(pin_out, set_base, set_cnt, {11'd0, instr[4:0]});
              3'd1: n_x = {11'd0, instr[4:0]};
              3'd2: n_y = {11'd0, instr[4:0]};
              3'd4: n_pin_dir = wr_range(pin_dir, set_base, set_cnt, {11'd0, instr[4:0]});
              default: ;
            endcase
          end

          default: ;
        endcase
      end

      // Side-set happens on every issue and wins over other pin writes.
      if (side_valid) begin
        if (side_dirs) n_pin_dir = wr_range(n_pin_dir, side_base, {3'b000, side_cnt}, {13'd0, side_val});
        else n_pin_out = wr_range(n_pin_out, side_base, {3'b000, side_cnt}, {13'd0, side_val});
      end

      if (!stall) begin
        if (jump) n_pc = jtarget;
        else if (!exec_valid) n_pc = (pc == wrap_top) ? wrap_bot : pc + 6'd1;
        n_exec_valid = exec_new;
        n_delay      = exec_new ? 5'd0 : dly;
      end
    end

    // G2 emission into the ISR; conflicting instructions stalled above.
    if (g2_emit) begin
      emit_b = lfsr_feed[1] ? lfsr_dout : g2_emit_bit;
      isr_e  = in_right ? {emit_b, isr[15:1]} : {isr[14:0], emit_b};
      cnt_e  = sat16({1'b0, isr_cnt} + 6'd1);
      if (emit_lfsr) n_lfsr = lfsr_next;
      if (autopush && cnt_e >= push_thr) begin
        if (rx_full) rx_drop = 1'b1;
        else begin
          e_rx_push = 1'b1;
          e_rx_data = isr_e;
        end
        n_isr     = 16'h0000;
        n_isr_cnt = 5'd0;
      end else begin
        n_isr     = isr_e;
        n_isr_cnt = cnt_e;
      end
    end
  end

  // ============================================================ registers
  wire lfsr_host_wr = wr & (off[4:2] == 3'b110);  // 0x18-0x1B

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pc         <= 6'd0;
      x          <= 16'd0;
      y          <= 16'd0;
      isr        <= 16'd0;
      isr_cnt    <= 5'd0;
      osr        <= 16'd0;
      osr_cnt    <= SIXTEEN;
      delay_cnt  <= 5'd0;
      exec_valid <= 1'b0;
      exec_instr <= 16'd0;
      irqw       <= 1'b0;
      stalled    <= 1'b0;
      pin_out    <= 13'd0;
      pin_dir    <= 13'd0;
      lfsr_val   <= 32'd0;
    end else begin
      pc      <= n_pc;
      x       <= n_x;
      y       <= n_y;
      pin_out <= n_pin_out;
      pin_dir <= n_pin_dir;
      if (restart) begin
        isr        <= 16'd0;
        isr_cnt    <= 5'd0;
        osr        <= 16'd0;
        osr_cnt    <= SIXTEEN;
        delay_cnt  <= 5'd0;
        exec_valid <= 1'b0;
        irqw       <= 1'b0;
        stalled    <= 1'b0;
      end else begin
        isr        <= n_isr;
        isr_cnt    <= n_isr_cnt;
        osr        <= n_osr;
        osr_cnt    <= n_osr_cnt;
        delay_cnt  <= n_delay;
        exec_valid <= n_exec_valid;
        exec_instr <= n_exec_instr;
        irqw       <= n_irqw;
        if (issue) stalled <= stall;
      end
      // A forced instruction from the Host wins over the engine.
      if (forced_wr) begin
        exec_instr <= {bus_wdata, bus_lo_hold};
        exec_valid <= 1'b1;
      end
      // A Host write wins over an engine update in the same cycle.
      if (lfsr_host_wr) begin
        case (off[1:0])
          2'd0: lfsr_val <= {lfsr_val[31:8], bus_wdata};
          2'd1: lfsr_val <= {lfsr_val[31:16], bus_wdata, lfsr_val[7:0]};
          2'd2: lfsr_val <= {lfsr_val[31:24], bus_wdata, lfsr_val[15:0]};
          default: lfsr_val <= {bus_wdata, lfsr_val[23:0]};
        endcase
      end else begin
        lfsr_val <= n_lfsr;
      end
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) div_cnt <= 16'd0;
    else if (!enable || restart || div_sync || div_tick) div_cnt <= 16'd0;
    else div_cnt <= div_cnt + 16'd1;
  end

  // ========================================================= edge capture
  reg  cap_prev;
  wire cap_in = pins_in[cap_pin];
  wire cap_ev = (cap_edge[0] & cap_in & ~cap_prev) | (cap_edge[1] & ~cap_in & cap_prev);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cap_prev   <= 1'b0;
      capture    <= 32'd0;
      st_cap_ovr <= 1'b0;
    end else begin
      cap_prev   <= cap_in;
      st_cap_ovr <= cap_ev & cap_flag_en & flags[cap_flag];
      if (cap_ev) capture <= counter;
    end
  end

  wire [7:0] cap_flag_set = (cap_ev && cap_flag_en) ? (8'd1 << cap_flag) : 8'h00;

  // ============================================================== readback
  reg [15:0] dbg;
  always @(*) begin
    case (dbg_sel)
      3'd0: dbg = {2'b00, delay_cnt, irqw, exec_valid, stalled, pc};
      3'd1: dbg = x;
      3'd2: dbg = y;
      3'd3: dbg = isr;
      3'd4: dbg = osr;
      3'd5: dbg = {3'b000, osr_cnt, 3'b000, isr_cnt};
      3'd6: dbg = instr;
      default: dbg = div_cnt;
    endcase
  end

  always @(*) begin
    bus_rdata = 8'h00;
    if (hit) begin
      case (off)
        5'h00: bus_rdata = clkdiv[7:0];
        5'h01: bus_rdata = clkdiv[15:8];
        5'h02: bus_rdata = {2'b00, wrap_bot};
        5'h03: bus_rdata = {2'b00, wrap_top};
        5'h04: bus_rdata = {1'b0, fifo_mode, out_right, in_right, autopull, autopush};
        5'h05: bus_rdata = {pull_thr_r, push_thr_r};
        5'h06: bus_rdata = {out_cnt_r, out_base};
        5'h07: bus_rdata = {1'b0, set_cnt_r, set_base};
        5'h08: bus_rdata = {jmp_pin, in_base};
        5'h09: bus_rdata = {side_dirs, side_opt, side_cnt, side_base};
        5'h0A: bus_rdata = {1'b0, jmp_pat, jmp_base};
        5'h0B: bus_rdata = {3'b000, status_sel, status_n};
        5'h0C: bus_rdata = {3'b000, lfsr_right, lfsr_mode, lfsr_feed};
        5'h0D: bus_rdata = {1'b0, cap_flag_en, cap_edge, cap_pin};
        5'h0E: bus_rdata = {5'b00000, cap_flag};
        5'h0F: bus_rdata = bus_hi ? dbg[15:8] : dbg[7:0];
        5'h10: bus_rdata = bus_hi ? rx_read_hi : rx_head[7:0];
        5'h11: bus_rdata = {rx_level, tx_level};
        5'h12: bus_rdata = {5'b00000, ram_addr};
        5'h13: bus_rdata = bus_hi ? ram_rdata_h[15:8] : ram_rdata_h[7:0];
        5'h14: bus_rdata = lfsr_poly[7:0];
        5'h15: bus_rdata = lfsr_poly[15:8];
        5'h16: bus_rdata = lfsr_poly[23:16];
        5'h17: bus_rdata = lfsr_poly[31:24];
        5'h18: bus_rdata = lfsr_val[7:0];
        5'h19: bus_rdata = lfsr_val[15:8];
        5'h1A: bus_rdata = lfsr_val[23:16];
        5'h1B: bus_rdata = lfsr_val[31:24];
        5'h1C: bus_rdata = capture[7:0];
        5'h1D: bus_rdata = capture[15:8];
        5'h1E: bus_rdata = capture[23:16];
        default: bus_rdata = capture[31:24];
      endcase
    end
  end

  assign flag_set = i_flag_set | cap_flag_set;

endmodule
