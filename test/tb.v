`default_nettype none
`timescale 1ns / 1ps

/* Testbench: instantiates the design and models the board around the
   bidirectional pins. Test peers drive a pin by setting ext_oe/ext_out;
   pull_up gives a released line a high level. A line nobody drives and
   without a pull-up reads 0. `contention` flags any bit where one side
   drives 1 and the other drives 0.
*/
module tb ();

  initial begin
    $dumpfile("tb.fst");
    $dumpvars(0, tb);
    #1;
  end

  reg clk;
  reg rst_n;
  reg ena;
  reg [7:0] ui_in;
  wire [7:0] uio_in;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  // Board model for uio pins.
  reg [7:0] ext_oe;
  reg [7:0] ext_out;
  reg [7:0] pull_up;

  wire [7:0] drive0 = (uio_oe & ~uio_out) | (ext_oe & ~ext_out);
  wire [7:0] drive1 = (uio_oe & uio_out) | (ext_oe & ext_out);
  wire [7:0] contention = drive0 & drive1;
  assign uio_in = ~drive0 & (drive1 | pull_up);

  tt_um_jedinakdev_protocol_emulator user_project (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

endmodule
