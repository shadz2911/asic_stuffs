/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// State-machine clock divider (see 3.5.5 Clock Dividers).
// Produces a one-cycle ENABLE pulse every div clk cycles - never a clock.
// Integer divide only (CLKDIV.INT); the RP2040 also has an 8-bit fraction.
module clk_div (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,  // while low: no ticks, counter held
    input  wire [15:0] div,     // runtime divider: 1 = tick every clk cycle, 0 = 65536
    output wire        tick     // one-cycle pulse every div clk cycles
);

  // Wrap point, computed at 16 bits so div = 0 gives 16'hFFFF,
  // i.e. a 65536-cycle period like the RP2040.
  // NOTE: div is now a runtime value - change it only while disabled. If it drops
  // below the current count, counter == TOP is missed until the 16-bit wrap.
  wire [15:0] TOP = div - 16'd1;

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
