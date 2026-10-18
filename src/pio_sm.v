/*
 * Copyright (c) 2024 Your Name
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// One PIO state machine (RP2040 datasheet chapter 3: 3.4 Instruction Set,
// 3.5 Functional Details).
//
// EXECUTION RULE (every behaviour below hangs off this):
//   On each clock-divider tick:
//     - if delay_cnt > 0, decrement it and do nothing else;
//     - otherwise execute the instruction at pc.
//   A STALLED instruction (e.g. PULL on an empty TX FIFO, WAIT not yet
//   satisfied) changes NO state and does NOT start its delay; it simply
//   re-executes on the next tick.
//   A COMPLETED instruction loads delay_cnt from instruction bits [12:8]
//   (minus the bits used by side-set, see sideset_count) and updates pc,
//   wrapping wrap_top -> wrap_bottom unless a JMP was taken.
//
// Clocking: everything runs on clk. The divided rate is NOT a clock - it is a
// one-cycle enable pulse (clkdiv_tick) used as a clock enable.
module pio_sm (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        enable,       // state machine enabled (CTRL.SM_ENABLE)

    // Runtime configuration, same bit layout as the RP2040's SMx_* registers
    // (datasheet 3.7 List of Registers) so SDK-style config values copy straight over.
    // Change only while the SM is disabled.
    input  wire [31:0] clkdiv,       // SMx_CLKDIV
    input  wire [31:0] execctrl,     // SMx_EXECCTRL
    input  wire [31:0] shiftctrl,    // SMx_SHIFTCTRL
    input  wire [31:0] pinctrl,      // SMx_PINCTRL
    // Not an RP2040 register: there the SDK forces a JMP to the program's offset
    // through SMx_INSTR. Here pc simply resets to this value.
    input  wire [4:0]  start_pc,

    // Instruction fetch
    output wire [4:0]  instr_addr,   // = pc
    input  wire [15:0] instr_data,

    // TX FIFO read side (system -> state machine)
    output wire        tx_pop,
    input  wire [31:0] tx_dout,
    input  wire        tx_empty,

    // RX FIFO write side (state machine -> system)
    output wire        rx_push,
    output wire [31:0] rx_din,
    input  wire        rx_full,

    // Pins
    input  wire [7:0]  pins_in,
    output wire [7:0]  pins_out,
    output wire [7:0]  pins_oe
);

  // ------------------------------------------------------------------
  // Config fields (just renamed slices of the config words - use these names)
  // ------------------------------------------------------------------
  // SMx_CLKDIV (see 3.5.5 Clock Dividers)
  wire [15:0] clkdiv_int      = clkdiv[31:16];  // integer divider, 0 = 65536
  wire [7:0]  clkdiv_frac     = clkdiv[15:8];   // fractional part - not supported (ignored)

  // SMx_EXECCTRL (see 3.5.1 Side-set, 3.5.2 Program Wrapping)
  wire        side_en         = execctrl[30];     // side-set is `opt`: MSB of side-set field is an enable
  wire        side_pindir     = execctrl[29];     // side-set drives pindirs instead of pin values
  wire [4:0]  jmp_pin         = execctrl[28:24];  // pin tested by JMP PIN (see 3.4.2 JMP)
  wire [4:0]  wrap_top        = execctrl[16:12];
  wire [4:0]  wrap_bottom     = execctrl[11:7];

  // SMx_SHIFTCTRL (see 3.5.4 Autopush and Autopull, 3.4.4 IN, 3.4.5 OUT)
  wire [4:0]  pull_thresh     = shiftctrl[29:25]; // 0 means 32
  wire [4:0]  push_thresh     = shiftctrl[24:20]; // 0 means 32
  wire        out_shift_right = shiftctrl[19];
  wire        in_shift_right  = shiftctrl[18];
  wire        autopull        = shiftctrl[17];
  wire        autopush        = shiftctrl[16];

  // SMx_PINCTRL (see 3.5.6 GPIO Mapping)
  wire [2:0]  sideset_count   = pinctrl[31:29];   // includes the enable bit when side_en
  wire [2:0]  set_count       = pinctrl[28:26];   // at most 5
  wire [5:0]  out_count       = pinctrl[25:20];   // 0..32
  wire [4:0]  in_base         = pinctrl[19:15];
  wire [4:0]  sideset_base    = pinctrl[14:10];
  wire [4:0]  set_base        = pinctrl[9:5];
  wire [4:0]  out_base        = pinctrl[4:0];

  // ------------------------------------------------------------------
  // Fetch / sequencing / pin state (driven by the EXECUTION RULE block below)
  // ------------------------------------------------------------------
  reg [4:0]  pc;          // program counter, resets to start_pc (see 3.5.2)
  reg [4:0]  delay_cnt;   // delay cycles still to wait after the last completed instruction
  reg [7:0]  pin_out_reg; // pin output values (side-set, SET PINS; OUT/MOV PINS later)
  reg [7:0]  pin_oe_reg;  // pin directions    (side-set pindirs, SET PINDIRS; OUT later)
  reg [31:0] x;           // scratch X (SET now; MOV, JMP X-- later - see 3.4.10, 3.4.8, 3.4.2)
  reg [31:0] y;           // scratch Y
  reg [31:0] osr;              // output shift register (PULL loads, OUT shifts - see 3.4.7, 3.4.5)
  reg [5:0]  osr_shift_count;  // bits shifted out of osr, 0..32 (32 = empty)

  // ------------------------------------------------------------------
  // Architectural state: declared only, NOT yet driven. Each needs a reset
  // value and an update rule in a clkdiv_tick-gated always block.
  // ------------------------------------------------------------------
  /* verilator lint_off UNDRIVEN */
  reg [31:0] isr;              // TODO: input shift register (see 3.4.4 IN, 3.4.6 PUSH)
  reg [5:0]  isr_shift_count;  // TODO: bits shifted into isr, 0..32 (autopush compares to push_thresh)
  /* verilator lint_on UNDRIVEN */

  // Clock divider: clkdiv_tick is a one-cycle ENABLE pulse every clkdiv_int clk cycles.
  // Gate all state updates with it; never use it as a clock.
  wire clkdiv_tick;

  clk_div u_clk_div (
      .clk   (clk),
      .rst_n (rst_n),
      .enable(enable),
      .div   (clkdiv_int),
      .tick  (clkdiv_tick)
  );

  // ------------------------------------------------------------------
  // EXECUTION RULE: pc, delay and wrap
  // ------------------------------------------------------------------
  // Delay = the low (5 - sideset_count) bits of [12:8]; side-set owns the top bits
  // (see 3.5.1). A shift by a runtime value is fine - it is just a small mux.
  wire [4:0] delay_field = instr_data[12:8] & (5'b11111 >> sideset_count);

  // WAIT instruction handling

  reg to_wait;
  reg [4:0] wait_sum;  // 5 bits: wraps at 32 like the RP2040
  always @(*) begin
    to_wait = 1'b0;
    wait_sum = in_base + instr_data[4:0];
    case (instr_data[6:5])
      // pins 8+ don't exist and read as 0
      2'b00: to_wait = ((instr_data[4:3] != 2'b00) ? 1'b0 : pins_in[instr_data[2:0]]) != instr_data[7];
      2'b01: to_wait = ((wait_sum[4:3] != 2'b00)   ? 1'b0 : pins_in[wait_sum[2:0]])   != instr_data[7];
      2'b10: ; // TODO when IRQ implemented, will currently just complete
      default: ;
    endcase
  end

  // PULL instruction work
  wire is_pull = (instr_data[15:13] == 3'b100) && instr_data[7];
  wire osr_empty = osr_shift_count >= ((pull_thresh == 5'd0) ? 6'd32 : {1'b0, pull_thresh});
  wire pull_skip = is_pull && instr_data[6] && !osr_empty;
  wire stall = (is_pull && instr_data[5] && tx_empty && !pull_skip)
    || ((instr_data[15:13] == 3'b001) && to_wait);

  // JMP taken assignment
  
  reg jmp_taken;
  wire [4:0] jmp_target;
  assign jmp_target = instr_data[4:0];

  // Only a JMP (opcode 000) can be taken: other opcodes use bits [7:5] for other things
  wire is_jmp = (instr_data[15:13] == 3'b000);

  always @(*) begin
    jmp_taken = 1'b0;
    if (is_jmp) begin
      case (instr_data[7:5])
        3'b000: jmp_taken = 1'b1;
        3'b001: jmp_taken = x == 0;
        3'b010: jmp_taken = x != 0;
        3'b011: jmp_taken = y == 0;
        3'b100: jmp_taken = y != 0;
        3'b101: jmp_taken = x != y;
        // jmp_pin is 0..31 but only 8 pins exist: pins 8+ read as 0
        3'b110: jmp_taken = (jmp_pin < 5'd8) ? pins_in[jmp_pin[2:0]] : 1'b0;
        3'b111: jmp_taken = !osr_empty;  // !OSRE
      endcase
    end
  end
  
  // OUT istruction handling

  // bit count 0 means 32
  wire [31:0] osr_data =
      (instr_data[4:0] == 5'd0) ? osr :
      out_shift_right ? (osr & ~(32'hFFFFFFFF << instr_data[4:0]))
                      : (osr >> (6'd32 - {1'b0, instr_data[4:0]}));

  // IN instruction handling
  reg [31:0] isr_data, in_src, in_data;
  reg [4:0]  in_pin;   // pin feeding bit b of the PINS source; wraps at 32
  integer    b;
  always @(*) begin
    in_src = 32'd0;
    in_pin = 5'd0;
    case (instr_data[7:5])
      // bit b = pin in_base + b; pins 8+ read 0
      3'b000:
        for (b = 0; b < 32; b = b + 1) begin
          in_pin    = in_base + b[4:0];
          in_src[b] = (in_pin[4:3] == 2'b00) ? pins_in[in_pin[2:0]] : 1'b0;
        end
      3'b001: in_src = x;
      3'b010: in_src = y;
      3'b011: in_src = 32'd0;
      3'b100: ;
      3'b101: ;
      3'b110: in_src = isr;
      3'b111: in_src = osr;
    endcase
    // bit count 0 means 32: the whole source replaces the ISR
    in_data = (instr_data[4:0] == 5'd0) ? in_src : in_src & ~(32'hFFFFFFFF << instr_data[4:0]);
    isr_data = (instr_data[4:0] == 5'd0) ? in_data :
      in_shift_right ? (isr >> instr_data[4:0]) | (in_data << (32-instr_data[4:0]))
      : (isr << instr_data[4:0]) | (in_data);
  end

  // Where pc goes when the current instruction completes. A taken JMP overrides
  // wrap, so a JMP placed at wrap_top still jumps (see 3.5.2).
  wire [4:0] next_pc = jmp_taken          ? jmp_target  :
                       (instr_data[15:13] == 3'b011) && (instr_data[7:5] == 3'b101) ? osr_data[4:0] :
                       (pc == wrap_top)   ? wrap_bottom :
                                            pc + 5'd1;

  integer p;  // pin loop index for side-set / SET (unrolled at synthesis, not a register)

  // Side-set numbers widened to 32 bits so they mix cleanly with the integer p
  wire [31:0] ss_base  = {27'd0, sideset_base};                        // first side-set pin
  wire [31:0] ss_npins = {29'd0, sideset_count} - {31'd0, side_en};    // pins driven (opt bit excluded)
  wire [31:0] ss_lsb   = 32'd13 - {29'd0, sideset_count};              // instr_data bit of value bit 0

  // SET pin numbers, widened the same way (value bit 0 is instr_data[0])
  wire [31:0] set_base32  = {27'd0, set_base};                         // first SET pin
  wire [31:0] set_count32 = {29'd0, set_count};                        // pins written (max 5)

  // OUT pin numbers, widened the same way
  wire [31:0] out_base32  = {27'd0, out_base};                         // first OUT pin
  wire [31:0] out_count32 = {26'd0, out_count};                        // pins written (0..32)

  always @(posedge clk) begin
    if (!rst_n) begin
      pc          <= start_pc;
      delay_cnt   <= 5'd0;
      pin_out_reg <= 8'h00;
      pin_oe_reg  <= 8'h00;
      x           <= 32'd0;
      y           <= 32'd0;
      osr         <= 32'd0;
      osr_shift_count <= 6'd32;
      isr <= 32'd0;
      isr_shift_count <= 6'd0;
    end else if (clkdiv_tick) begin
      if (delay_cnt != 5'd0) begin
        // Still in the previous instruction's delay: just count down
        delay_cnt <= delay_cnt - 5'd1;
      end else begin
        if (!stall) begin
            // Instruction completes: advance and start its delay.
            // A stalled instruction changes nothing and does not start its delay.
            pc        <= next_pc;
            delay_cnt <= delay_field;

            // SET INSTRUCTION DECODING
            if (instr_data[15:13] == 3'b111) begin
                case (instr_data[7:5])
                    // PINS: data bit i -> pin set_base + i, for set_count pins.
                    // Same pattern as side-set: walk all 8 pins, write those in the window.
                    3'b000:
                        for (p = 0; p < 8; p = p + 1)
                            if (p >= set_base32 && p - set_base32 < set_count32)
                                pin_out_reg[p] <= instr_data[p - set_base32];
                    3'b001: x <= {{27{1'b0}}, instr_data[4:0]};
                    3'b010: y <= {{27{1'b0}}, instr_data[4:0]};
                    // PINDIRS: same, into the direction register (1 = output)
                    3'b100:
                        for (p = 0; p < 8; p = p + 1)
                            if (p >= set_base32 && p - set_base32 < set_count32)
                                pin_oe_reg[p] <= instr_data[p - set_base32];
                    default: ;  // 011, 101, 110, 111 are reserved: no effect
                endcase
            end

            // JMP INSTRUCTION DECODING
            if (instr_data[15:13] == 3'b000) begin
              if (instr_data[7:5] == 3'b010) x <= x -1;
              if (instr_data[7:5] == 3'b100) y <= y -1;
            end

            // PULL INSTRUCTION DECODING
            if (is_pull && ~pull_skip) begin
              if (!tx_empty) begin
                osr <= tx_dout;
                osr_shift_count <= 0;
              end else begin
                osr <= x;
                osr_shift_count <= 0;
              end
            end

            // OUT INSTRUCTION DECODING
            if (instr_data[15:13] == 3'b011) begin
              osr_shift_count <= (instr_data[4:0] == 5'd0) ||
                                 (osr_shift_count + {1'b0, instr_data[4:0]} > 6'd32) ? 6'd32 :
                                 osr_shift_count + {1'b0, instr_data[4:0]};
              osr <= instr_data[4:0] == 5'd0 ? 32'd0 :
                out_shift_right ? (osr >> instr_data[4:0])
                : (osr << instr_data[4:0]);
              case (instr_data[7:5])
                3'b000:
                  for (p = 0; p < 8; p = p + 1)
                    if (p >= out_base32 && p - out_base32 < out_count32)
                      pin_out_reg[p] <= osr_data[p - out_base32];
                3'b001: x <= osr_data;
                3'b010: y <= osr_data;
                3'b100:
                  for (p = 0; p < 8; p = p + 1)
                    if (p >= out_base32 && p - out_base32 < out_count32)
                      pin_oe_reg[p] <= osr_data[p - out_base32];
                3'b110: ; // TODO (not needed for UART)
                3'b111: ; // TODO (not needed for UART)
                default: ;
              endcase
            end

            // IN INSTRUCTION DECODING
            if (instr_data[15:13] == 3'b010) begin
              isr <= isr_data;
              isr_shift_count <= (instr_data[4:0] == 5'd0) ||
                                 (isr_shift_count + {1'b0, instr_data[4:0]} > 6'd32) ? 6'd32 :
                                 isr_shift_count + {1'b0, instr_data[4:0]};
            end
        end
        // SIDESET assignment happens after SET assignment
        // Side-set: applied on issue, stalled or not. Copy the value bits straight
        // out of the instruction onto pins sideset_base, sideset_base+1, ...
        //   value bit i = instr_data[13 - sideset_count + i]   (i = 0 .. sideset_count-side_en-1)
        //   uart_tx (count 2, opt): i = 0 only -> pin sideset_base <= instr_data[11]
        if ((sideset_count != 0) && (!side_en || instr_data[12])) begin
            // Base and width are runtime values, so walk all 8 pins one bit at a
            // time (a variable single-bit index is legal; a variable [hi:lo] is not)
            for (p = 0; p < 8; p = p + 1) begin
                if (p >= ss_base && p - ss_base < ss_npins) begin
                    if (side_pindir)
                        pin_oe_reg[p]  <= instr_data[ss_lsb + p - ss_base];
                    else
                        pin_out_reg[p] <= instr_data[ss_lsb + p - ss_base];
                end
            end
        end
      end
    end
  end

  // Stub tie-offs: drive every output so the design compiles and lints cleanly.
  assign instr_addr = pc;       // fetch address is the pc (combinational instruction memory read)
  assign tx_pop     = is_pull && clkdiv_tick && (delay_cnt == 0) && !tx_empty && !pull_skip;
  assign rx_push    = 1'b0;
  assign rx_din     = 32'd0;
  assign pins_out   = pin_out_reg;
  assign pins_oe    = pin_oe_reg;

  // Unused inputs (remove entries as the real logic uses them)
  wire _unused = &{rx_full,
                   isr, isr_shift_count,
                   push_thresh, in_shift_right, autopull, autopush,
                   // reserved / unsupported register bits
                   clkdiv[7:0], execctrl[31], execctrl[23:17], execctrl[6:0], shiftctrl[31:30],
                   shiftctrl[15:0], 1'b0};

endmodule
