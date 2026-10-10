/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Two-flop synchroniser: brings an asynchronous pad input into the clk domain
// (the RP2040 does the same on every GPIO input, see 3.5.6 GPIO Mapping).
// Adds 2 clk cycles of latency.
module sync2 #(
    parameter WIDTH = 1,
    // Value both stages take in reset; match each line's idle level so no
    // false edge is seen coming out of reset (UART idles high, hence all-1s default)
    parameter [WIDTH-1:0] RESET_VAL = {WIDTH{1'b1}}
) (
    input  wire             clk,
    input  wire             rst_n,
    input  wire [WIDTH-1:0] d,   // asynchronous input
    output wire [WIDTH-1:0] q    // synchronised to clk
);

  // two-FF synchronizer
  reg [WIDTH-1:0] stage_1, stage_2;

  always @(posedge clk) begin
    if (!rst_n) begin
        stage_1 <= RESET_VAL;
        stage_2 <= RESET_VAL;
    end else begin
        stage_1 <= d;
        stage_2 <= stage_1;
    end
  end

  assign q = stage_2;

endmodule
