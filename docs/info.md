<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This chip runs communication protocols as small programs rather than fixed logic. It has two programmable state machines based on the RP2040's PIO, which share a 32-word instruction memory. Each state machine has its own 4-word TX and RX FIFOs and clock divider.

A host microcontroller controls the chip over SPI (mode 0, MSB first). It loads programs, writes configuration, starts the state machines and exchanges data through the FIFOs. So far, UART transmit and receive have been tested, in simulation only.

Write frames are `[1 aaaaaaa][32 data bits]`. Read frames are `[0 aaaaaaa][8 dummy bits][32 data bits]`. Keeping CS_n low continues a burst to the same address.

| Address | Register | Access | Purpose |
| --- | --- | --- | --- |
| `0x00-0x1F` | `INSTR` | W | Instruction memory |
| `0x20` | `CTRL` | R/W | Bit 0: SM0 enable. Bit 1: SM1 enable |
| `0x21` | `FSTAT` | R | FIFO flags: TX empty `[25:24]`, TX full `[17:16]`, RX empty `[9:8]`, RX full `[1:0]` |
| `0x30` / `0x40` | `TXF` | W | Push a word to the TX FIFO |
| `0x31` / `0x41` | `RXF` | R | Read and pop a word from the RX FIFO |
| `0x32` / `0x42` | `CLKDIV` | R/W | RP2040 layout; integer part only |
| `0x33` / `0x43` | `EXECCTRL` | R/W | RP2040 layout |
| `0x34` / `0x44` | `SHIFTCTRL` | R/W | RP2040 layout |
| `0x35` / `0x45` | `PINCTRL` | R/W | RP2040 layout |
| `0x36` / `0x46` | `START_PC` | R/W | Program start address |
| `0x38` / `0x48` | `PINS_INIT` | R/W | Pin levels while the state machine is disabled |

## How to test

Connect the host's SPI pins, then:

1. Write the assembled program to `INSTR`.
2. Write the configuration registers while the state machine is disabled.
3. Set its enable bit in `CTRL`.
4. Use `FSTAT`, `TXF` and `RXF` to exchange data.

For UART transmit, each byte pushed to `SM0_TXF` comes out of `uo[0]`. For UART receive, frames on `ui[0]` arrive in `SM1_RXF`, with the byte in bits `[31:24]`.

To run the simulation tests, run `make -B` in the `test` directory.

## External hardware

A host microcontroller with SPI. For UART, a device connected to `uo[0]` and `ui[0]`.
