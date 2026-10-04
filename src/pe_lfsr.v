/*
 * Copyright (c) 2026 JediNakDev
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// One step of the programmable checksum shift register (G1). Purely
// combinational; the engine holds the registers. Modes are defined in
// docs/spec.md.
module pe_lfsr (
    input  wire [31:0] val,
    input  wire [31:0] poly,
    input  wire [1:0]  mode,
    input  wire        right,
    input  wire        din,
    output reg         dout,
    output reg  [31:0] next
);

  wire        top = right ? val[0] : val[31];
  wire [31:0] sh  = right ? {1'b0, val[31:1]} : {val[30:0], 1'b0};
  wire [31:0] ins = right ? 32'h8000_0000 : 32'h0000_0001;
  wire        par = ^(val & poly);

  always @(*) begin
    case (mode)
      2'd0: begin
        dout = din;
        next = sh ^ ((top ^ din) ? poly : 32'd0);
      end
      2'd1: begin
        dout = din ^ par;
        next = sh | ((din ^ par) ? ins : 32'd0);
      end
      2'd2: begin
        dout = din ^ par;
        next = sh | (din ? ins : 32'd0);
      end
      default: begin
        dout = din ^ par;
        next = sh | (par ? ins : 32'd0);
      end
    endcase
  end

endmodule
