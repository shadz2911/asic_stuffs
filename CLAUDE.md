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
PYTHONPATH= COCOTB_TEST_FILTER=test_project make -B  # run one test (regex on the test name)
make hex                                             # only assemble programs into build/uart.hex
gtkwave tb.fst tb.gtkw                               # view waveforms after a run
make -B GATES=yes                                    # gate-level; needs PDK_ROOT and test/gate_level_netlist.v
```

- `PYTHONPATH=` clears a ROS path on the main dev machine that otherwise injects a pytest plugin and prints a misleading traceback.
- `make` exits 0 even when tests fail. Read the `TESTS=` summary, or `grep failure results.xml` as CI does.

Lint and strict compile, from the repo root:

```sh
verilator --lint-only -Wall -DSIM --top-module tt_um_example src/*.v
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

`src/project.v` (`tt_um_example`) is structural only: it has Tiny Tapeout's fixed port list, wires the blocks together, and holds the current config as constants. It contains two `pio_sm` instances (SM0 = UART TX on `uo_out[0]`, SM1 = UART RX from `ui_in[0]`), one TX and one RX `fifo` per SM, and one `instr_mem` with a read port per SM.

### Program flow across files

`programs/*.pio` → `scripts/assemble.py` → `test/build/uart.hex` → `tb.PROGRAM_HEX` → `tt_um_example.PROGRAM_HEX` → `instr_mem.INIT_FILE` (`$readmemh`, only under `SIM`).

The assembler packs programs back to back and relocates JMP targets, because JMP addresses are absolute. It prints each program's `START_PC`, wrap points and `SIDESET_COUNT`. Those values are hardcoded in `project.v`'s `SMx_*` constants and `start_pc` connections, so changing the program order in `test/Makefile` means updating them too.

### No host interface yet

The instruction memory write port, the system side of every FIFO, `enable` and all config are tied off in `project.v`. On silicon the chip cannot be used until a host interface drives them. In simulation, memory comes from the preload, so SM0 stalls forever on `pull` with an empty TX FIFO, and received words stay in SM1's RX FIFO unless a test reads them through the hierarchy.

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

- `info.yaml` still has `tiles: "1x1"` (the competition uses `6x4`), template `project` metadata, and the template module name `tt_um_example`, which must become unique.
- `test/test.py` only checks reset behaviour. There is no permanent test for UART TX or RX yet.

## Reference

The RP2040 datasheet, chapter 3, is the behavioural spec. `docs/*.md` holds short per-instruction notes; `docs/info.md` is the Tiny Tapeout datasheet source.
