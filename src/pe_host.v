/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Host port: SPI mode 0 target, 1 or 4 bits wide, oversampled by clk.
// Turns transactions into a byte-wide register bus. See docs/spec.md.
//
// Bus timing: bus_we and bus_commit are one-cycle pulses with bus_addr,
// bus_hi and bus_wdata valid in the same cycle. For reads, bus_rdata is
// sampled in the cycle bus_fetch is high, and must be side-effect free;
// destructive effects (FIFO pop) happen on bus_commit, after the byte has
// been shifted out completely.
module pe_host (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       hcs_n,
    input  wire       hsck,
    input  wire [3:0] hdi,
    output wire [3:0] hdo,
    input  wire       quad_cfg,
    input  wire [7:0] status_byte,
    output reg  [6:0] bus_addr,
    output reg  [7:0] bus_wdata,
    output reg  [7:0] bus_lo_hold,
    output reg        bus_hi,
    output reg        bus_we,
    output reg        bus_commit,
    output reg        bus_fetch,
    input  wire [7:0] bus_rdata
);

  // Synchronizers. Data uses the same depth as the clock so they stay aligned.
  reg [1:0] cs_s;
  reg [2:0] sck_s;
  reg [3:0] d_s0, d_s1;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      cs_s  <= 2'b11;
      sck_s <= 3'b000;
      d_s0  <= 4'b0000;
      d_s1  <= 4'b0000;
    end else begin
      cs_s  <= {cs_s[0], hcs_n};
      sck_s <= {sck_s[1:0], hsck};
      d_s0  <= hdi;
      d_s1  <= d_s0;
    end
  end

  wire active = ~cs_s[1];
  wire rise   = sck_s[1] & ~sck_s[2];

  // Addresses that do not auto-increment.
  function is_port;
    input [6:0] a;
    begin
      is_port = (a == 7'h0B) || (a == 7'h0D) ||
                (a[6] && (a[4:0] == 5'h0F || a[4:0] == 5'h10 || a[4:0] == 5'h13));
    end
  endfunction

  localparam [1:0] ST_IDLE = 2'd0, ST_ADDR = 2'd1, ST_FETCH = 2'd2, ST_STATUS = 2'd3;

  reg       active_q;
  reg       quad;
  reg [2:0] bitcnt;
  reg [6:0] shin;
  reg       have_cmd;
  reg       is_rd;
  reg [6:0] addr;
  reg       hi;
  reg [7:0] out_shift;
  reg [1:0] stage;

  wire       done    = quad ? (bitcnt == 3'd4) : (bitcnt == 3'd7);
  wire [7:0] in_byte = quad ? {shin[3:0], d_s1} : {shin[6:0], d_s1[0]};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      active_q    <= 1'b0;
      quad        <= 1'b0;
      bitcnt      <= 3'd0;
      shin        <= 7'h00;
      have_cmd    <= 1'b0;
      is_rd       <= 1'b0;
      addr        <= 7'h00;
      hi          <= 1'b0;
      out_shift   <= 8'h00;
      stage       <= ST_IDLE;
      bus_addr    <= 7'h00;
      bus_wdata   <= 8'h00;
      bus_lo_hold <= 8'h00;
      bus_hi      <= 1'b0;
      bus_we      <= 1'b0;
      bus_commit  <= 1'b0;
      bus_fetch   <= 1'b0;
    end else begin
      active_q   <= active;
      bus_we     <= 1'b0;
      bus_commit <= 1'b0;
      bus_fetch  <= 1'b0;

      if (!active) begin
        have_cmd  <= 1'b0;
        bitcnt    <= 3'd0;
        out_shift <= 8'h00;
        stage     <= ST_IDLE;
      end else if (!active_q) begin
        // Transaction start: latch the width and present STATUS.
        quad      <= quad_cfg;
        bitcnt    <= 3'd0;
        have_cmd  <= 1'b0;
        out_shift <= status_byte;
        stage     <= ST_IDLE;
      end else begin
        if (rise) begin
          shin <= in_byte[6:0];
          if (done) begin
            bitcnt <= 3'd0;
            if (!have_cmd) begin
              have_cmd <= 1'b1;
              is_rd    <= in_byte[7];
              addr     <= in_byte[6:0];
              hi       <= 1'b0;
              stage    <= in_byte[7] ? ST_ADDR : ST_STATUS;
            end else begin
              bus_addr <= addr;
              bus_hi   <= hi;
              if (is_rd) begin
                bus_commit <= 1'b1;
                stage      <= ST_ADDR;
              end else begin
                bus_we    <= 1'b1;
                bus_wdata <= in_byte;
                if (!hi) bus_lo_hold <= in_byte;
                stage <= ST_STATUS;
              end
              if (is_port(addr)) hi <= ~hi;
              else addr <= addr + 7'd1;
            end
          end else begin
            bitcnt    <= bitcnt + (quad ? 3'd4 : 3'd1);
            out_shift <= quad ? {out_shift[3:0], 4'h0} : {out_shift[6:0], 1'b0};
          end
        end

        case (stage)
          ST_ADDR: begin
            // Any commit is applied at the end of this cycle; fetch after it.
            bus_addr  <= addr;
            bus_hi    <= hi;
            bus_fetch <= 1'b1;
            stage     <= ST_FETCH;
          end
          ST_FETCH: begin
            out_shift <= bus_rdata;
            stage     <= ST_IDLE;
          end
          ST_STATUS: begin
            out_shift <= status_byte;
            stage     <= ST_IDLE;
          end
          default: ;
        endcase
      end
    end
  end

  assign hdo = !active ? 4'h0 : (quad ? out_shift[7:4] : {3'b000, out_shift[7]});

endmodule
