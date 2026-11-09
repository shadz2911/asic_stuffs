/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_shadz2911_pio #(
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
  //   SM0 = UART TX (programs/uart_tx.pio,      words 0..3) -> uio[0]
  //   SM1 = UART RX (programs/uart_rx_mini.pio, words 4..7) <- uio[1]
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
  wire [7:0] pins_in;  // uio_in after the synchroniser; PIO pin N = uio[N]
  wire [7:0] sm0_pins_out, sm0_pins_oe, sm1_pins_out, sm1_pins_oe;

  // ---- IRQ flags ----
  wire [2:0] sm0_addr_irq, sm1_addr_irq;
  wire       sm0_we_irq, sm1_we_irq;
  wire       sm0_wdata_irq, sm1_wdata_irq;
  wire       sm0_rdata_irq, sm1_rdata_irq;
  wire [7:0] host_clear_irq;
  wire [7:0] host_flags_irq;

  // ---- Host interface: host_spi -> host_regs ----
  wire        host_req, host_wr;
  wire [6:0]  host_addr;
  wire [31:0] host_data, host_rdata;
  wire        host_miso;

  // ---- Host interface: host_regs -> rest of the chip ----
  wire        mem_we;
  wire [4:0]  mem_waddr;
  wire [15:0] mem_wdata;
  wire        host_sm0_tx_push;
  wire [31:0] host_sm0_tx_din;
  wire        host_sm1_tx_push;
  wire [31:0] host_sm1_tx_din;
  wire        host_sm0_rx_pop, host_sm1_rx_pop;
  wire [1:0]  sm_enable;
  wire [31:0] sm0_clkdiv, sm0_execctrl, sm0_shiftctrl, sm0_pinctrl;
  wire [31:0] sm1_clkdiv, sm1_execctrl, sm1_shiftctrl, sm1_pinctrl;
  wire [4:0]  sm0_start_pc, sm1_start_pc;
  wire [7:0]  sm0_pins_init, sm1_pins_init;
  wire [7:0]  sm0_pindirs_init, sm1_pindirs_init;

  sync2 #(.WIDTH(8)) sync_pin_ins (
    .clk(clk),
    .rst_n(rst_n),
    .d(uio_in),
    .q(pins_in)
  );

  host_spi u_host_spi (
      .sck  (ui_in[7]),
      .cs_n (ui_in[6]),
      .mosi (ui_in[5]),
      .clk  (clk),
      .rst_n(rst_n),
      .req  (host_req),
      .wr   (host_wr),
      .addr (host_addr),
      .data (host_data),
      .rdata(host_rdata),
      .miso (host_miso)
  );

  host_regs u_host_regs (
      .clk        (clk),
      .rst_n      (rst_n),
      .req        (host_req),
      .we         (host_wr),
      .addr       (host_addr),
      .wdata      (host_data),
      .rdata      (host_rdata),
      .clear_irq  (host_clear_irq),
      .rdata_irq  (host_flags_irq),
      .mem_we     (mem_we),
      .mem_waddr  (mem_waddr),
      .mem_wdata  (mem_wdata),
      .sm0_tx_push(host_sm0_tx_push),
      .sm0_tx_din (host_sm0_tx_din),
      .sm1_tx_push(host_sm1_tx_push),
      .sm1_tx_din (host_sm1_tx_din),
      .sm0_rx_dout (sm0_rx_dout),
      .sm0_rx_empty(sm0_rx_empty),
      .sm0_rx_full (sm0_rx_full),
      .sm0_tx_empty(sm0_tx_empty),
      .sm0_tx_full (sm0_tx_full),
      .sm0_rx_pop  (host_sm0_rx_pop),
      .sm1_rx_dout (sm1_rx_dout),
      .sm1_rx_empty(sm1_rx_empty),
      .sm1_rx_full (sm1_rx_full),
      .sm1_tx_empty(sm1_tx_empty),
      .sm1_tx_full (sm1_tx_full),
      .sm1_rx_pop  (host_sm1_rx_pop),
      .sm_enable  (sm_enable),
      .sm0_clkdiv   (sm0_clkdiv),
      .sm0_execctrl (sm0_execctrl),
      .sm0_shiftctrl(sm0_shiftctrl),
      .sm0_pinctrl  (sm0_pinctrl),
      .sm0_start_pc (sm0_start_pc),
      .sm0_pins_init(sm0_pins_init),
      .sm0_pindirs_init(sm0_pindirs_init),
      .sm1_clkdiv   (sm1_clkdiv),
      .sm1_execctrl (sm1_execctrl),
      .sm1_shiftctrl(sm1_shiftctrl),
      .sm1_pinctrl  (sm1_pinctrl),
      .sm1_start_pc (sm1_start_pc),
      .sm1_pins_init(sm1_pins_init),
      .sm1_pindirs_init(sm1_pindirs_init)
  );

  instr_mem #(
      .INIT_FILE(PROGRAM_HEX)
  ) u_instr_mem (
      .clk   (clk),
      .we    (mem_we),          // TODO: program loader (not decided)
      .waddr (mem_waddr),
      .wdata (mem_wdata),
      .raddr0(sm0_instr_addr),
      .rdata0(sm0_instr_data),
      .raddr1(sm1_instr_addr),
      .rdata1(sm1_instr_data)
  );

  // ---------------- SM0: UART TX ----------------
  fifo #(.WIDTH(32), .DEPTH(4)) u_sm0_tx_fifo (
      .clk(clk), .rst_n(rst_n),
      .push (host_sm0_tx_push),           // TODO: system side - bytes to transmit (not decided)
      .din  (host_sm0_tx_din),
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
      .pop  (host_sm0_rx_pop),
      .dout (sm0_rx_dout),
      .empty(sm0_rx_empty),
      .full (sm0_rx_full),
      .level(sm0_rx_level)
  );

  pio_sm u_sm0 (
      .clk       (clk),
      .rst_n     (rst_n),
      .enable    (sm_enable[0]),        // TODO: host-controlled enable (not decided)
      .sm_num    (2'd0),
      .clkdiv    (sm0_clkdiv),
      .execctrl  (sm0_execctrl),
      .shiftctrl (sm0_shiftctrl),
      .pinctrl   (sm0_pinctrl),
      .start_pc  (sm0_start_pc),
      .pins_init (sm0_pins_init),
      .pindirs_init(sm0_pindirs_init),
      .instr_addr(sm0_instr_addr),
      .instr_mem (sm0_instr_data),
      .tx_pop    (sm0_tx_pop),
      .tx_dout   (sm0_tx_dout),
      .tx_empty  (sm0_tx_empty),
      .rx_push   (sm0_rx_push),
      .rx_din    (sm0_rx_din),
      .rx_full   (sm0_rx_full),
      .pins_in   (pins_in),
      .pins_out  (sm0_pins_out),
      .pins_oe   (sm0_pins_oe),
      .addr_irq  (sm0_addr_irq),
      .we_irq    (sm0_we_irq),
      .wdata_irq (sm0_wdata_irq),
      .rdata_irq (sm0_rdata_irq)
  );

  // ---------------- SM1: UART RX ----------------
  fifo #(.WIDTH(32), .DEPTH(4)) u_sm1_tx_fifo (  // unused by uart_rx_mini
      .clk(clk), .rst_n(rst_n),
      .push (host_sm1_tx_push),
      .din  (host_sm1_tx_din),
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
      .pop  (host_sm1_rx_pop),
      .dout (sm1_rx_dout),
      .empty(sm1_rx_empty),
      .full (sm1_rx_full),
      .level(sm1_rx_level)
  );

  pio_sm u_sm1 (
      .clk       (clk),
      .rst_n     (rst_n),
      .enable    (sm_enable[1]),        // TODO: host-controlled enable (not decided)
      .sm_num    (2'd1),
      .clkdiv    (sm1_clkdiv),
      .execctrl  (sm1_execctrl),
      .shiftctrl (sm1_shiftctrl),
      .pinctrl   (sm1_pinctrl),
      .start_pc  (sm1_start_pc),
      .pins_init (sm1_pins_init),
      .pindirs_init(sm1_pindirs_init),
      .instr_addr(sm1_instr_addr),
      .instr_mem (sm1_instr_data),
      .tx_pop    (sm1_tx_pop),
      .tx_dout   (sm1_tx_dout),
      .tx_empty  (sm1_tx_empty),
      .rx_push   (sm1_rx_push),
      .rx_din    (sm1_rx_din),
      .rx_full   (sm1_rx_full),
      .pins_in   (pins_in),
      .pins_out  (sm1_pins_out),
      .pins_oe   (sm1_pins_oe),
      .addr_irq  (sm1_addr_irq),
      .we_irq    (sm1_we_irq),
      .wdata_irq (sm1_wdata_irq),
      .rdata_irq (sm1_rdata_irq)
  );

  // ---------------- IRQ flags, shared by both SMs ----------------
  irq_flags #(.NUM_SMS(2)) u_irq_flags (
      .clk  (clk),
      .rst_n(rst_n),
      .addr ({sm1_addr_irq, sm0_addr_irq}),
      .we   ({sm1_we_irq, sm0_we_irq}),
      .wdata({sm1_wdata_irq, sm0_wdata_irq}),
      .host_clear(host_clear_irq),
      .flags_out (host_flags_irq),
      .rdata({sm1_rdata_irq, sm0_rdata_irq})
  );

  pin_mux uio_mux (
    .sm_pins_out({sm1_pins_out, sm0_pins_out}),
    .sm_pins_oe({sm1_pins_oe, sm0_pins_oe}),
    .pins_out(uio_out),
    .pins_oe(uio_oe)
  );

  // ---- Outputs ----
  assign uo_out  = {host_miso, 7'b0000000};  // uo_out[7] = host MISO

  // List all unused inputs to prevent warnings
  wire _unused = &{ena, ui_in[4:0],
                   sm0_tx_level, sm0_rx_level, sm1_tx_level, sm1_rx_level, 1'b0};

endmodule
