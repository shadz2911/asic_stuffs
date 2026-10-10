/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Synchronous FIFO, used for every TX and RX FIFO (see 3.2 Programmer's Model,
// 3.5.4 Autopush and Autopull). Single clock, active-low reset.
module fifo #(
    parameter WIDTH = 32,
    parameter DEPTH = 4
) (
    input  wire                   clk,
    input  wire                   rst_n,
    input  wire                   push,   // write din
    input  wire [WIDTH-1:0]       din,
    input  wire                   pop,    // remove the oldest entry
    output wire [WIDTH-1:0]       dout,   // oldest entry
    output wire                   empty,
    output wire                   full,
    output wire [$clog2(DEPTH):0] level   // entries held, 0..DEPTH
);

  localparam PTR_WIDTH = $clog2(DEPTH);

  // create fifo memory
  reg [WIDTH-1:0] mem [0:DEPTH-1];

  // declare pointers
  reg [PTR_WIDTH:0] wr_ptr, rd_ptr;

  // || pop ensures correct behavior for simultaneous pop/pushes when full/empty
  always @(posedge clk) begin
    if (!rst_n) begin
        wr_ptr <= 0;
        rd_ptr <= 0;
    end else begin
        if (push && (!full || pop)) begin
            mem[wr_ptr[PTR_WIDTH-1:0]] <= din;
            wr_ptr <= wr_ptr + 1;
        end
        if (pop && !empty) begin
            rd_ptr <= rd_ptr + 1;
        end
     end
  end

  // combinational read
  assign dout = mem[rd_ptr[PTR_WIDTH-1:0]];

  // use wrap bit to determine full vs empty
  assign empty = wr_ptr == rd_ptr;
  assign full = wr_ptr == {~rd_ptr[PTR_WIDTH], rd_ptr[PTR_WIDTH-1:0]};
  assign level = wr_ptr - rd_ptr;

endmodule
