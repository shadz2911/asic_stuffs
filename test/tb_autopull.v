`timescale 1ns/1ps
// Standalone unit test for pio_sm autopull. Run from the test/ folder:
//   iverilog -g2012 -o autopull_sim tb_autopull.v ../src/pio_sm.v ../src/clk_div.v ../src/fifo.v && vvp autopull_sim
module tb_autopull;
  reg clk = 0, rst_n = 0, enable = 0;
  always #5 clk = ~clk;

  // Config (driven only while the SM is disabled/in reset)
  reg [15:0] instr     = 16'h6001;       // OUT PINS, 1
  reg [31:0] clkdiv    = 32'h0001_0000;  // divide by 1
  reg [31:0] shiftctrl = 32'h100A_0000;  // pull_thresh=8, shift right, autopull on
  reg [31:0] pinctrl   = 32'h0010_0000;  // out_base=0, out_count=1

  wire [4:0]  instr_addr;
  wire        tx_pop, tx_empty, tx_full, rx_push;
  wire [31:0] tx_dout, rx_din;
  wire [2:0]  tx_level;
  wire [7:0]  pins_out, pins_oe;
  reg         push = 0;
  reg  [31:0] din  = 0;

  fifo #(.WIDTH(32), .DEPTH(4)) u_fifo (
      .clk(clk), .rst_n(rst_n), .push(push), .din(din), .pop(tx_pop),
      .dout(tx_dout), .empty(tx_empty), .full(tx_full), .level(tx_level));

  pio_sm dut (
      .clk(clk), .rst_n(rst_n), .enable(enable),
      .clkdiv(clkdiv), .execctrl(32'h0001_F000),
      .shiftctrl(shiftctrl), .pinctrl(pinctrl),
      .start_pc(5'd0), .pins_init(8'hFF),
      .instr_addr(instr_addr), .instr_data(instr),   // same instruction at every address
      .tx_pop(tx_pop), .tx_dout(tx_dout), .tx_empty(tx_empty),
      .rx_push(rx_push), .rx_din(rx_din), .rx_full(1'b0),
      .pins_in(8'h00), .pins_out(pins_out), .pins_oe(pins_oe));

  // ---------------- checker ----------------
  // Everything below reads values at posedge, i.e. BEFORE that edge's updates,
  // and the stimulus only changes at negedge, so there are no races.
  reg [31:0] words [0:3];
  integer width = 8, n_out = 1, words_total = 0;
  reg check_en = 0;
  integer ncommits = 0, npops = 0, errors = 0;
  reg commit_q = 0;
  integer idx;
  reg [31:0] mask, expv;

  always @(posedge clk) begin
    // commit_q says "an instruction committed at the previous edge", so
    // pins_out now shows its result.
    if (commit_q) begin
      if (check_en) begin
        if (ncommits * n_out < words_total * width) begin
          idx  = ncommits * n_out;
          mask = (32'd1 << n_out) - 1;
          expv = (words[idx / width] >> (idx % width)) & mask;
          if ((pins_out & mask[7:0]) !== expv[7:0]) begin
            errors = errors + 1;
            $display("  MISMATCH commit %0d: pins=%b expected=%b",
                     ncommits, pins_out & mask[7:0], expv[7:0]);
          end
        end else begin
          errors = errors + 1;
          $display("  UNEXPECTED commit %0d after the data ran out (stall failed)", ncommits);
        end
      end
      ncommits = ncommits + 1;
    end
    commit_q <= rst_n && enable && dut.clkdiv_tick && (dut.delay_cnt == 0) && !dut.stall;
    if (rst_n && tx_pop) npops = npops + 1;
  end

  // ---------------- helpers ----------------
  task do_reset;
    begin
      @(negedge clk); enable = 0; push = 0; rst_n = 0;
      repeat (3) @(negedge clk);
      ncommits = 0; npops = 0; words_total = 0; check_en = 0;
      rst_n = 1;
      @(negedge clk);
    end
  endtask

  task set_cfg(input [15:0] div, input [31:0] sc, input [31:0] pc, input [15:0] ins,
              input integer w, input integer n, input chk);
    begin
      clkdiv = {div, 16'h0000}; shiftctrl = sc; pinctrl = pc; instr = ins;
      width = w; n_out = n; check_en = chk;
    end
  endtask

  task push_word(input [31:0] w);
    begin
      words[words_total] = w; words_total = words_total + 1;  // record before the FIFO sees it
      @(negedge clk); din = w; push = 1;
      @(negedge clk); push = 0;
    end
  endtask

  task wait_cycles(input integer c);
    begin repeat (c) @(negedge clk); end
  endtask

  task expect_eq(input [8*24-1:0] what, input integer got, input integer exp);
    begin
      if (got !== exp) begin
        errors = errors + 1;
        $display("  FAIL %0s: got %0d expected %0d", what, got, exp);
      end else
        $display("  ok   %0s = %0d", what, got);
    end
  endtask

  // ---------------- tests ----------------
  initial begin
    $dumpfile("autopull.vcd"); $dumpvars(0, tb_autopull);

    // A: divide by 1, threshold 8, OUT PINS,1. Three bytes back to back, then the
    //    FIFO runs dry (must stall), then a fourth byte (must resume).
    $display("A: div=1 thresh=8 OUT PINS,1");
    do_reset; set_cfg(1, 32'h100A_0000, 32'h0010_0000, 16'h6001, 8, 1, 1);
    push_word(32'hA5); push_word(32'h3C); push_word(32'h81);
    @(negedge clk); enable = 1;
    wait_cycles(80);
    expect_eq("A commits (3 bytes)", ncommits, 24);
    expect_eq("A fifo pops",         npops, 3);
    push_word(32'hD2);
    wait_cycles(40);
    expect_eq("A commits (4 bytes)", ncommits, 32);
    expect_eq("A fifo pops",         npops, 4);

    // B: same, divide by 3. tx_pop must still be one pulse per refill.
    $display("B: div=3 thresh=8 OUT PINS,1");
    do_reset; set_cfg(3, 32'h100A_0000, 32'h0010_0000, 16'h6001, 8, 1, 1);
    push_word(32'hA5); push_word(32'h3C); push_word(32'h81);
    @(negedge clk); enable = 1;
    wait_cycles(240);
    expect_eq("B commits (3 bytes)", ncommits, 24);
    expect_eq("B fifo pops",         npops, 3);
    push_word(32'hD2);
    wait_cycles(120);
    expect_eq("B commits (4 bytes)", ncommits, 32);
    expect_eq("B fifo pops",         npops, 4);

    // C: autopull OFF. No refill, no pops; OUT must not stall.
    $display("C: autopull off");
    do_reset; set_cfg(1, 32'h1008_0000, 32'h0010_0000, 16'h6001, 8, 1, 0);
    push_word(32'hA5);
    @(negedge clk); enable = 1;
    wait_cycles(60);
    expect_eq("C fifo pops",         npops, 0);
    expect_eq("C kept running",      ncommits > 40, 1);

    // D: threshold field 0 means 32 bits per word.
    $display("D: thresh=0 (32 bits)");
    do_reset; set_cfg(1, 32'h000A_0000, 32'h0010_0000, 16'h6001, 32, 1, 1);
    push_word(32'hDEADBEEF); push_word(32'h12345678);
    @(negedge clk); enable = 1;
    wait_cycles(150);
    expect_eq("D commits (2 words)", ncommits, 64);
    expect_eq("D fifo pops",         npops, 2);

    // E: OUT PINS,8 with 8 output pins. Every OUT drains the OSR, so every OUT refills.
    $display("E: OUT PINS,8 (refill every OUT)");
    do_reset; set_cfg(1, 32'h100A_0000, 32'h0080_0000, 16'h6008, 8, 8, 1);
    push_word(32'hA5); push_word(32'h3C); push_word(32'h81);
    @(negedge clk); enable = 1;
    wait_cycles(40);
    expect_eq("E commits",           ncommits, 3);
    expect_eq("E fifo pops",         npops, 3);

    if (errors == 0) $display("PASS"); else $display("FAILED: %0d errors", errors);
    $finish;
  end
endmodule