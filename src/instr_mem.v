/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// PIO instruction memory: 32 x 16-bit, shared by both state machines, as on the
// RP2040 (see 3.2 Programmer's Model). One write port, two combinational read
// ports so each state machine can fetch the instruction at its own pc every cycle.
module instr_mem #(
    // Hex image loaded in simulation only (scripts/assemble.py output). "" = zeroed.
    parameter INIT_FILE = ""
) (
    input  wire        clk,

    // Write port (future program loader)
    input  wire        we,
    input  wire [4:0]  waddr,
    input  wire [15:0] wdata,

    // Read port 0 (state machine 0)
    input  wire [4:0]  raddr0,
    output wire [15:0] rdata0,

    // Read port 1 (state machine 1)
    input  wire [4:0]  raddr1,
    output wire [15:0] rdata1
);

  // Storage. Declared now because the SIM preload below needs it.
  // No SRAM macro on Tiny Tapeout, so this becomes 512 flops.
  reg [15:0] mem [0:31];

  always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
  end

  assign rdata0 = mem[raddr0];
  assign rdata1 = mem[raddr1];

`ifdef SIM
  // Simulation-only preload, since there is no loader yet.
  // Zero first so unprogrammed words read 0 rather than X, then overlay the file.
  integer i;
  initial begin
    for (i = 0; i < 32; i = i + 1) mem[i] = 16'h0000;
    if (INIT_FILE != "") $readmemh(INIT_FILE, mem);
  end
`endif

endmodule
