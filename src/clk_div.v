/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// State-machine clock divider (see 3.5.5 Clock Dividers).
// Produces a one-cycle ENABLE pulse every DIV clk cycles - never a clock.
// Integer divide only; the RP2040 has a 16.8 fixed-point divider.
module clk_div #(
    parameter [15:0] DIV = 16'd1   // 1 = tick every clk cycle
) (
    input  wire clk,
    input  wire rst_n,
    input  wire enable,   // while low: no ticks, counter held
    output wire tick      // one-cycle pulse every DIV clk cycles
);

  // Wrap point, computed at 16 bits so DIV = 0 gives 16'hFFFF,
  // i.e. a 65536-cycle period like the RP2040.
  localparam [15:0] TOP = DIV - 1;

  reg [15:0] counter;

  always @(posedge clk) begin
    if (!rst_n) begin
        counter <= 0;
    end else if (enable) begin
        // Only count (and wrap) while enabled; otherwise hold
        counter <= (counter == TOP) ? 16'd0 : counter + 16'd1;
    end
  end

  // Gated by enable so a disabled state machine never sees a tick
  assign tick = enable && (counter == TOP);

endmodule
