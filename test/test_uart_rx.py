# SPDX-License-Identifier: Apache-2.0
"""End-to-end UART RX: UART frames on ui_in[0] in, words read over SPI out.

As with the TX tests, everything is driven and judged at the chip's pins.
"""

import random

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from test_uart_tx import (
    ADDR_CTRL,
    TICKS_PER_BIT,
    configure_uart_tx,
    load_program,
    reset_chip,
)

ADDR_FSTAT = 0x21
ADDR_SM1_RXF = 0x41
ADDR_SM1_CLKDIV = 0x42
ADDR_SM1_EXECCTRL = 0x43
ADDR_SM1_SHIFTCTRL = 0x44
ADDR_SM1_PINCTRL = 0x45
ADDR_SM1_START_PC = 0x46
CTRL_SM0_ENABLE = 0x1
CTRL_SM1_ENABLE = 0x2

# uart_rx_mini config: wrap 4-7, shift right, autopush at 8, IN and WAIT on pin 0
UART_RX_EXECCTRL = 0x00007200
UART_RX_SHIFTCTRL = 0x008D0000
UART_RX_PINCTRL = 0x00000000
UART_RX_START_PC = 4

FSTAT_ALL_EMPTY = 0x03000300
FSTAT_SM1_RX_EMPTY = 1 << 9
FSTAT_SM1_RX_FULL = 1 << 1

READ_HALF_PERIOD = 4  # SCK = clk/8


async def configure_uart_rx(host, clkdiv=1):
    await host.write(ADDR_SM1_CLKDIV, clkdiv << 16)
    await host.write(ADDR_SM1_EXECCTRL, UART_RX_EXECCTRL)
    await host.write(ADDR_SM1_SHIFTCTRL, UART_RX_SHIFTCTRL)
    await host.write(ADDR_SM1_PINCTRL, UART_RX_PINCTRL)
    await host.write(ADDR_SM1_START_PC, UART_RX_START_PC)


async def start_uart_rx(dut, clkdiv=1):
    host = await reset_chip(dut, READ_HALF_PERIOD)
    await load_program(host)
    await configure_uart_rx(host, clkdiv)
    await host.write(ADDR_CTRL, CTRL_SM1_ENABLE)
    await ClockCycles(dut.clk, 20 * clkdiv)
    return host


async def send_frame(dut, host, value, clkdiv=1):
    """Plays the external UART device: start bit, 8 data bits LSB first, stop bit."""
    cycles_per_bit = TICKS_PER_BIT * clkdiv
    for level in [0] + [(value >> i) & 1 for i in range(8)] + [1]:
        host.set_rx_line(level)
        await ClockCycles(dut.clk, cycles_per_bit, rising=False)


async def read_rx_byte(host, timeout_polls=200):
    """What firmware would do: wait until FSTAT shows data, then read and unpack it."""
    for _ in range(timeout_polls):
        if not await host.read(ADDR_FSTAT) & FSTAT_SM1_RX_EMPTY:
            word = await host.read(ADDR_SM1_RXF)
            assert word & 0x00FFFFFF == 0, f"low bits of RX word not clear: {word:#010x}"
            return word >> 24
    assert False, "FSTAT never showed received data"


@cocotb.test()
async def test_fstat_after_reset(dut):
    host = await reset_chip(dut, READ_HALF_PERIOD)
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
async def test_single_byte_and_fstat_at_each_stage(dut):
    host = await start_uart_rx(dut)
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY, "not empty before any frame"

    await send_frame(dut, host, ord("A"))
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY & ~FSTAT_SM1_RX_EMPTY, "frame didn't reach the FIFO"

    assert await host.read(ADDR_SM1_RXF) == ord("A") << 24
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY, "read didn't pop the FIFO"


@cocotb.test()
async def test_every_byte_value(dut):
    host = await start_uart_rx(dut)
    for value in range(256):
        await send_frame(dut, host, value)
        assert await read_rx_byte(host) == value


@cocotb.test()
async def test_one_read_pops_exactly_one_word(dut):
    host = await start_uart_rx(dut)
    await send_frame(dut, host, 0x11)
    await send_frame(dut, host, 0x22)

    assert await host.read(ADDR_SM1_RXF) == 0x11 << 24
    assert not await host.read(ADDR_FSTAT) & FSTAT_SM1_RX_EMPTY, "read popped more than one word"
    assert await host.read(ADDR_SM1_RXF) == 0x22 << 24
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
async def test_fifo_fills_and_drains_in_order(dut):
    host = await start_uart_rx(dut)
    message = [0xDE, 0xAD, 0xBE, 0xEF]
    for value in message:
        await send_frame(dut, host, value)
    assert await host.read(ADDR_FSTAT) & FSTAT_SM1_RX_FULL, "FSTAT not full after 4 words"

    for value in message:
        assert await host.read(ADDR_SM1_RXF) == value << 24
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
async def test_overrun_keeps_the_oldest_words(dut):
    """A fifth byte has nowhere to go, but the four already queued must survive."""
    host = await start_uart_rx(dut)
    message = [0x01, 0x02, 0x03, 0x04]
    for value in message + [0x05]:
        await send_frame(dut, host, value)
    assert [await host.read(ADDR_SM1_RXF) for _ in message] == [value << 24 for value in message]


@cocotb.test()
async def test_read_burst(dut):
    host = await start_uart_rx(dut)
    for value in (0x48, 0x69, 0x21):
        await send_frame(dut, host, value)
    assert await host.read(ADDR_SM1_RXF, count=3) == [0x48 << 24, 0x69 << 24, 0x21 << 24]
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
async def test_abort_in_dummy_byte_loses_nothing(dut):
    host = await start_uart_rx(dut)
    await send_frame(dut, host, 0x5A)
    for dummy_bits in (0, 4, 7):
        await host.aborted_read(ADDR_SM1_RXF, dummy_bits)
    assert await host.read(ADDR_SM1_RXF) == 0x5A << 24


@cocotb.test()
@cocotb.parametrize(bits_after_command=[8, 9, 20, 38, 39])
async def test_abort_during_data_bits_loses_nothing(dut, bits_after_command):
    """The pop happens on the 32nd data bit, so any earlier abort must keep the word."""
    host = await start_uart_rx(dut)
    await send_frame(dut, host, 0x5A)
    await host.aborted_read(ADDR_SM1_RXF, bits_after_command)
    assert await host.read(ADDR_SM1_RXF) == 0x5A << 24
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
@cocotb.parametrize(half_period=[10, 8, 6, 5, 4, 3])
async def test_read_speed(dut, half_period):
    """SCK = clk / (2 * half_period). Burst reads fail at clk/4, so clk/6 is the limit."""
    host = await start_uart_rx(dut)
    for value in (0x48, 0x69, 0x21, 0xA5):
        await send_frame(dut, host, value)

    host.half_period = half_period
    assert await host.read(ADDR_CTRL) == CTRL_SM1_ENABLE
    assert await host.read(ADDR_SM1_CLKDIV) == 1 << 16
    assert await host.read(ADDR_SM1_RXF) == 0x48 << 24
    assert await host.read(ADDR_SM1_RXF, count=3) == [0x69 << 24, 0x21 << 24, 0xA5 << 24]
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY


@cocotb.test()
async def test_receives_correctly_after_overrun(dut):
    """Overrun loses data, but draining the FIFO must leave the receiver working."""
    host = await start_uart_rx(dut)
    message = [0x01, 0x02, 0x03, 0x04]
    for value in message + [0x05]:
        await send_frame(dut, host, value)
    assert [await host.read(ADDR_SM1_RXF) for _ in message] == [value << 24 for value in message]

    # The overrun byte may have completed once space appeared; it's unreliable, so discard it
    await ClockCycles(dut.clk, TICKS_PER_BIT * 4)
    if not await host.read(ADDR_FSTAT) & FSTAT_SM1_RX_EMPTY:
        await host.read(ADDR_SM1_RXF)
    assert await host.read(ADDR_FSTAT) == FSTAT_ALL_EMPTY

    for value in (0x6C, 0x93):
        await send_frame(dut, host, value)
        assert await read_rx_byte(host) == value


@cocotb.test()
@cocotb.parametrize(clkdiv=[2, 3, 54])
async def test_clock_divider(dut, clkdiv):
    host = await start_uart_rx(dut, clkdiv)
    await send_frame(dut, host, 0xA5, clkdiv)
    assert await read_rx_byte(host) == 0xA5


@cocotb.test()
async def test_random_bytes_and_gaps(dut):
    host = await start_uart_rx(dut)
    for _ in range(100):
        value = random.randrange(256)
        await send_frame(dut, host, value)
        await ClockCycles(dut.clk, random.randrange(1, 100))
        assert await read_rx_byte(host) == value


async def loopback(dut, host):
    """Connects the chip's TX pin to its own RX pin, as a wire on the board would."""
    while True:
        await FallingEdge(dut.clk)
        host.set_rx_line(dut.uo_out.value.to_unsigned() & 1)


@cocotb.test()
async def test_tx_to_rx_loopback(dut):
    """Bytes sent over SPI go out of TX, back in through RX and are read over SPI."""
    host = await reset_chip(dut, READ_HALF_PERIOD)
    await load_program(host)
    await configure_uart_tx(host)
    await configure_uart_rx(host)

    cocotb.start_soon(loopback(dut, host))
    await ClockCycles(dut.clk, 10)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE | CTRL_SM1_ENABLE)

    message = b"Hello, loopback!"
    for value in message:
        await host.write(0x30, value)
        assert await read_rx_byte(host) == value
