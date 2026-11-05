# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An RP2040-PIO-style programmable protocol emulator ASIC for the Jane Street competition, built on the Tiny Tapeout IHP CMOS5L Verilog template. The chip must be reprogrammable after fabrication: UART, SPI and I2C first. Submission deadline: 2027-01-18. Verification methodology is judged.

## Commands

Tests run from `test/` with the repo's venv on `PATH`:

```sh
python3 -m venv .venv && source .venv/bin/activate && pip install -r test/requirements.txt   # first time
cd test
PYTHONPATH= make -B                                  # assemble programs, run all cocotb tests
PYTHONPATH= COCOTB_TEST_FILTER=test_read_burst make -B  # run matching tests (regex on the test name)
make hex                                             # only assemble programs into build/uart.hex
gtkwave tb.fst tb.gtkw                               # view waveforms after a run
make -B GATES=yes                                    # gate-level; needs PDK_ROOT and test/gate_level_netlist.v
```

- `PYTHONPATH=` clears a ROS path on the main dev machine that otherwise injects a pytest plugin and prints a misleading traceback.
- `COCOTB_TEST_FILTER` passes through a shell, so parentheses and pipes in the regex break the command.
- `python3 scripts/check_tests_catch_bugs.py` (venv active, from anywhere) adds known bugs to temporary copies of the repo and reports any the tests miss. Add a bug to its list when adding a feature.
- Failing tests made local `make` runs exit non-zero, but the template's CI comment says not to rely on that, so CI also runs `grep failure results.xml`. Read the `TESTS=` summary.

Lint and strict compile, from the repo root:

```sh
verilator --lint-only -Wall -DSIM --top-module tt_um_shadz2911_pio src/*.v
iverilog -g2005 -Wall -DSIM -o /dev/null src/*.v
```

cocotb compiles with `-g2012`, so a passing test does not prove the RTL is Verilog-2005; use the `iverilog -g2005` line for that. Also lint without `-DSIM`, because the Tiny Tapeout flow does. `DECLFILENAME` on `project.v` is expected.

GDS hardening runs in the `gds` GitHub Action (`TinyTapeout/tt-gds-action@ihp-cmos5l`) on push.

## RTL rules

- Verilog-2005 with `` `default_nettype none ``. Declare signals before use.
- One clock domain: `clk` only, with synchronous active-low `rst_n`. No derived or gated clocks; slow timing comes from the `clk_div` enable pulse.
- Pass every external input through `sync2` before logic reads it.
- FIFO pushes and pops and memory writes are one-cycle strobes.
- New source files must be added to `source_files` in `info.yaml` by hand. The Makefile picks up `src/*.v` automatically, despite the older comment in `info.yaml`.

## Architecture

`src/project.v` (`tt_um_shadz2911_pio`) is structural only: it has Tiny Tapeout's fixed port list and wires the blocks together. It contains two `pio_sm` instances, one TX and one RX `fifo` per SM, one `instr_mem` with a read port per SM, and the host interface (`host_spi` + `host_regs`). Current pins: `ui_in[7:5]` = SPI SCK, CS_n, MOSI; `uo_out[7]` = MISO; `ui_in[0]` feeds PIO pin 0 (UART RX); `uo_out[0]` is SM0's pin 0 (UART TX).

### Program flow across files

`programs/*.pio` → `scripts/assemble.py` → `test/build/uart.hex`. The tests read that file and load it over SPI. It also reaches `instr_mem.INIT_FILE` through `tb.PROGRAM_HEX` for the `$readmemh` preload, which exists only under `SIM`, so a test can pass on the preload alone unless it overwrites the memory first.

The assembler packs programs back to back and relocates JMP targets, because JMP addresses are absolute. It prints each program's `START_PC`, wrap points and `SIDESET_COUNT`. The tests hardcode the matching config words (`UART_TX_*`, `UART_RX_*`), so changing the program order in `test/Makefile` means updating them too.

### Host interface

The MCU controls everything over SPI mode 0, MSB first. `src/host_regs.v` is the source of truth for the register map.

- **Layers:** `host_spi` handles the frame and raises `req` with `wr`, `addr` and `data`. `host_regs` turns those into one-cycle strobes and held registers. `rdata` is combinational and follows `addr`.
- **Frames:** a write is `[1 aaaaaaa][32 data bits]`; a read is `[0 aaaaaaa][8 dummy bits][32 data bits on MISO]`. Holding CS_n low continues as a burst to the same address, and CS_n going high discards a partial frame.
- **Reads:** `req` means "the word has been sent", so it only triggers the pop on `RXF`. It fires on the last data bit, and `miso_shift` reloads one cycle later for a burst. Popping any earlier makes a single read lose the next word.
- **Registers:** `INSTR` `0x00-0x1F`, `CTRL` `0x20` (per-SM enable), `FSTAT` `0x21`. Each SM has a block at `0x30` / `0x40`: `TXF`, `RXF`, `CLKDIV`, `EXECCTRL`, `SHIFTCTRL`, `PINCTRL`, `START_PC`, then `PINS_INIT` at offset 8.
- **Startup:** SMs reset disabled. A disabled SM holds `pc` at `start_pc` and its pins at `PINS_INIT`, which resets to all high so UART lines idle correctly. Write config only while disabled.
- **SCK limits in simulation:** writes work at `clk/4`, but burst reads need `clk/6` or slower. That's with ideal pins and SCK lined up with `clk`; plan on `clk/8` for silicon.

### `pio_sm.v`

The header comment states the execution rule. An instruction is issued on a `clkdiv_tick` with `delay_cnt == 0`.

- **Stalls:** a stalled instruction changes nothing and starts no delay. Each `stall` term must be qualified by its own opcode, because bits `[7:5]` mean different things per instruction.
- **Side-set exception:** side-set applies on every issue, even when stalled. It comes after the `if (!stall)` block because the last non-blocking write wins, and side-set must beat OUT and SET on a shared pin.
- **Combinational vs clocked:** decisions needed in the same cycle (`jmp_taken`, `next_pc`, `stall`, `tx_pop`, `rx_push`) are combinational. State changes are in the clocked block, inside `if (!stall)`.
- **Runtime config:** `clkdiv`, `execctrl`, `shiftctrl` and `pinctrl` use the RP2040 `SMx_*` bit layout and are sliced into named wires at the top of the module. They are runtime signals, so don't use them as widths, part-select bounds or `generate` conditions. Variable-width pin writes loop over the 8 pins; variable bit extraction uses masks and shifts.
- **Encodings that are easy to miss:** a threshold or OUT/IN bit count of 0 means 32. Shifting 32 bits by 32 gives 0, so `n = 32` needs special-casing. Only 8 pins exist, so pin indexes of 8 or more read as 0.
- **Not implemented:** MOV, IRQ and `wait irq`, OUT/MOV EXEC, OUT to ISR, autopull, and the fractional clock divider.

### FIFO naming

`smN_tx_*` is system → SM and `smN_rx_*` is SM → system. With right-shift and autopush at 8, a received byte sits in bits `[31:24]` of the FIFO word.

## Known gaps before submission

- PIO pins aren't on `uio` yet and `pins_oe` is unused, so bidirectional protocols such as I2C can't run, and pins are always driven.
- `test/test_uart_tx.py` and `test/test_uart_rx.py` cover UART through the pins. SM1 has no TX test and SM0 has no RX test, and gate-level simulation hasn't been run.

## Reference

The RP2040 datasheet, chapter 3, is the behavioural spec. `docs/*.md` holds short per-instruction notes; `docs/info.md` is the Tiny Tapeout datasheet source.
