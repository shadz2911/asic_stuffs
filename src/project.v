/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_example #(
    // Simulation-only instruction memory image (scripts/assemble.py output), set by the testbench
    parameter PROGRAM_HEX = ""
) (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // Two state machines sharing one instruction memory, like the RP2040:
  //   SM0 = UART TX (programs/uart_tx.pio,      words 0..3) -> uo_out[0]
  //   SM1 = UART RX (programs/uart_rx_mini.pio, words 4..7) <- ui_in[0]
  // Each SM has its own TX and RX FIFO. No host interface yet, so the system
  // side of every FIFO is tied off.

  // ---- Instruction fetch ----
  wire [4:0]  sm0_instr_addr, sm1_instr_addr;
  wire [15:0] sm0_instr_data, sm1_instr_data;

  // ---- FIFOs: smN_tx = system -> SM, smN_rx = SM -> system ----
  wire        sm0_tx_pop, sm0_tx_empty, sm0_tx_full, sm0_rx_push, sm0_rx_empty, sm0_rx_full;
  wire [31:0] sm0_tx_dout, sm0_rx_din, sm0_rx_dout;
  wire [2:0]  sm0_tx_level, sm0_rx_level;
  wire        sm1_tx_pop, sm1_tx_empty, sm1_tx_full, sm1_rx_push, sm1_rx_empty, sm1_rx_full;
  wire [31:0] sm1_tx_dout, sm1_rx_din, sm1_rx_dout;
  wire [2:0]  sm1_tx_level, sm1_rx_level;

  // ---- Pins ----
  wire       uart_rx_sync;   // ui_in[0] after the synchroniser
  wire [7:0] pins_in = {7'b0000000, uart_rx_sync};  // pin 0 = UART RX; pins 7..1 not bonded out
  wire [7:0] sm0_pins_out, sm0_pins_oe, sm1_pins_out, sm1_pins_oe;

  // UART RX is asynchronous to clk: synchronise before any logic sees it
  sync2 #(.WIDTH(1)) u_sync_rx (
      .clk  (clk),
      .rst_n(rst_n),
      .d    (ui_in[0]),
      .q    (uart_rx_sync)
  );

  instr_mem #(
      .INIT_FILE(PROGRAM_HEX)
  ) u_instr_mem (
      .clk   (clk),
      .we    (1'b0),          // TODO: program loader (not decided)
      .waddr (5'd0),
      .wdata (16'd0),
      .raddr0(sm0_instr_addr),
      .rdata0(sm0_instr_data),
      .raddr1(sm1_instr_addr),
      .rdata1(sm1_instr_data)
  );

  // ---------------- SM0: UART TX ----------------
  fifo #(.WIDTH(32), .DEPTH(4)) u_sm0_tx_fifo (
      .clk(clk), .rst_n(rst_n),
      .push (1'b0),           // TODO: system side - bytes to transmit (not decided)
      .din  (32'd0),
      .pop  (sm0_tx_pop),
      .dout (sm0_tx_dout),
      .empty(sm0_tx_empty),
      .full (sm0_tx_full),
      .level(sm0_tx_level)
  );

  fifo #(.WIDTH(32), .DEPTH(4)) u_sm0_rx_fifo (  // unused by uart_tx
      .clk(clk), .rst_n(rst_n),
      .push (sm0_rx_push),
      .din  (sm0_rx_din),
      .pop  (1'b0),           // TODO: system side (not decided)
      .dout (sm0_rx_dout),
      .empty(sm0_rx_empty),
      .full (sm0_rx_full),
      .level(sm0_rx_level)
  );

  // Configuration from uart_tx_program_init() in pico-examples, as RP2040 register
  // words (field positions per datasheet 3.7). Constants for now.
  // TODO: replace with host-written config registers (host interface not decided).
  // TODO: the SDK also presets the TX pin to 1 and output before enabling, so the
  //       line idles high (UART idle) - reflect that in the pin register reset values.
  localparam [31:0] SM0_CLKDIV    = (32'd1 << 16);   // INT = 1. TODO: f_clk / (8 * baud) - not decided
  localparam [31:0] SM0_EXECCTRL  = (32'd1 << 30)    // SIDE_EN: `.side_set 1 opt`
                                  | (32'd3 << 12)    // WRAP_TOP    (from scripts/assemble.py)
                                  | (32'd0 << 7);    // WRAP_BOTTOM
  localparam [31:0] SM0_SHIFTCTRL = (32'd0 << 25)    // PULL_THRESH = 32 (encoded as 0)
                                  | (32'd1 << 19)    // OUT_SHIFTDIR = right (LSB first)
                                  | (32'd1 << 18);   // IN_SHIFTDIR  = right (unused here)
                                                     // AUTOPULL = 0: program does an explicit PULL
  localparam [31:0] SM0_PINCTRL   = (32'd2 << 29)    // SIDESET_COUNT = 2 (1 pin + enable bit)
                                  | (32'd1 << 20)    // OUT_COUNT = 1
                                  | (32'd0 << 10)    // SIDESET_BASE = 0 (TX pin)
                                  | (32'd0 << 0);    // OUT_BASE     = 0 (same TX pin)

  pio_sm u_sm0 (
      .clk       (clk),
      .rst_n     (rst_n),
      .enable    (1'b1),        // TODO: host-controlled enable (not decided)
      .clkdiv    (SM0_CLKDIV),
      .execctrl  (SM0_EXECCTRL),
      .shiftctrl (SM0_SHIFTCTRL),
      .pinctrl   (SM0_PINCTRL),
      .start_pc  (5'd0),        // program offset (from scripts/assemble.py)
      .instr_addr(sm0_instr_addr),
      .instr_data(sm0_instr_data),
      .tx_pop    (sm0_tx_pop),
      .tx_dout   (sm0_tx_dout),
      .tx_empty  (sm0_tx_empty),
      .rx_push   (sm0_rx_push),
      .rx_din    (sm0_rx_din),
      .rx_full   (sm0_rx_full),
      .pins_in   (pins_in),
      .pins_out  (sm0_pins_out),
      .pins_oe   (sm0_pins_oe)
  );

  // ---------------- SM1: UART RX ----------------
  fifo #(.WIDTH(32), .DEPTH(4)) u_sm1_tx_fifo (  // unused by uart_rx_mini
      .clk(clk), .rst_n(rst_n),
      .push (1'b0),           // TODO: system side (not decided)
      .din  (32'd0),
      .pop  (sm1_tx_pop),
      .dout (sm1_tx_dout),
      .empty(sm1_tx_empty),
      .full (sm1_tx_full),
      .level(sm1_tx_level)
  );

  fifo #(.WIDTH(32), .DEPTH(4)) u_sm1_rx_fifo (
      .clk(clk), .rst_n(rst_n),
      .push (sm1_rx_push),
      .din  (sm1_rx_din),
      .pop  (1'b0),           // TODO: system side - received bytes (not decided)
      .dout (sm1_rx_dout),
      .empty(sm1_rx_empty),
      .full (sm1_rx_full),
      .level(sm1_rx_level)
  );

  // Configuration from uart_rx_mini_program_init() in pico-examples.
  // Note: shifting right with autopush at 8 leaves the byte in bits [31:24] of
  // the pushed word (the SDK reads the FIFO's top byte).
  localparam [31:0] SM1_CLKDIV    = (32'd1 << 16);   // INT = 1. TODO: f_clk / (8 * baud) - not decided
  localparam [31:0] SM1_EXECCTRL  = (32'd7 << 12)    // WRAP_TOP    (from scripts/assemble.py)
                                  | (32'd4 << 7);    // WRAP_BOTTOM
  localparam [31:0] SM1_SHIFTCTRL = (32'd8 << 20)    // PUSH_THRESH = 8: push after each byte
                                  | (32'd1 << 19)    // OUT_SHIFTDIR = right (unused here)
                                  | (32'd1 << 18)    // IN_SHIFTDIR  = right (LSB first)
                                  | (32'd1 << 16);   // AUTOPUSH
  localparam [31:0] SM1_PINCTRL   = (32'd0 << 15);   // IN_BASE = 0: WAIT PIN 0 / IN PINS read the RX pin

  pio_sm u_sm1 (
      .clk       (clk),
      .rst_n     (rst_n),
      .enable    (1'b1),        // TODO: host-controlled enable (not decided)
      .clkdiv    (SM1_CLKDIV),
      .execctrl  (SM1_EXECCTRL),
      .shiftctrl (SM1_SHIFTCTRL),
      .pinctrl   (SM1_PINCTRL),
      .start_pc  (5'd4),        // program offset (from scripts/assemble.py)
      .instr_addr(sm1_instr_addr),
      .instr_data(sm1_instr_data),
      .tx_pop    (sm1_tx_pop),
      .tx_dout   (sm1_tx_dout),
      .tx_empty  (sm1_tx_empty),
      .rx_push   (sm1_rx_push),
      .rx_din    (sm1_rx_din),
      .rx_full   (sm1_rx_full),
      .pins_in   (pins_in),
      .pins_out  (sm1_pins_out),
      .pins_oe   (sm1_pins_oe)
  );

  // ---- Outputs ----
  assign uo_out  = {7'b0000000, sm0_pins_out[0]};  // uo_out[0] = UART TX
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, ui_in[7:1], uio_in,
                   sm0_tx_full, sm0_tx_level, sm0_rx_empty, sm0_rx_dout, sm0_rx_level,
                   sm1_tx_full, sm1_tx_level, sm1_rx_empty, sm1_rx_dout, sm1_rx_level,
                   sm0_pins_out[7:1], sm0_pins_oe, sm1_pins_out, sm1_pins_oe, 1'b0};

endmodule
