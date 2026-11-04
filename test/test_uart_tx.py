# SPDX-License-Identifier: Apache-2.0
"""End-to-end UART TX: SPI on the host pins in, UART frames on uo_out[0] out.

Everything is driven and judged at the chip's pins, so these tests never look
inside the design. Pins: ui_in[7] SCK, ui_in[6] CS_n, ui_in[5] MOSI, uo_out[0] TX.
"""

import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

ADDR_INSTR = 0x00
ADDR_CTRL = 0x20
ADDR_SM0_TXF = 0x30
CTRL_SM0_ENABLE = 0x1

# uart_tx takes 8 ticks per bit, and clkdiv is still the constant 1 in project.v
CYCLES_PER_BIT = 8
FRAME_BITS = 10  # start + 8 data + stop

PROGRAM_HEX = Path(__file__).parent / "build" / "uart.hex"


class HostSpi:
    """Models the MCU: SPI mode 0, MSB first, one 40-bit frame per write."""

    def __init__(self, dut, half_period=5):
        self.dut = dut
        self.half_period = half_period  # clk cycles per SCK phase; 2 is the clk/4 limit
        self._drive(sck=0, cs_n=1, mosi=0)

    def _drive(self, sck, cs_n, mosi):
        # ui_in[0] is the UART RX line, which idles high
        self.dut.ui_in.value = (sck << 7) | (cs_n << 6) | (mosi << 5) | 1

    async def _wait(self, cycles=None):
        await ClockCycles(self.dut.clk, self.half_period if cycles is None else cycles, rising=False)

    async def _shift(self, value, nbits):
        for i in range(nbits - 1, -1, -1):
            bit = (value >> i) & 1
            self._drive(sck=0, cs_n=0, mosi=bit)
            await self._wait()
            self._drive(sck=1, cs_n=0, mosi=bit)
            await self._wait()
        self._drive(sck=0, cs_n=0, mosi=0)

    async def _select(self):
        self._drive(sck=0, cs_n=0, mosi=0)
        await self._wait()

    async def _deselect(self):
        await self._wait()
        self._drive(sck=0, cs_n=1, mosi=0)
        await self._wait(4)  # let CS_n through the synchroniser

    async def write(self, addr, *words):
        """One write frame. Several words make a burst to the same address."""
        await self._select()
        await self._shift(0x80 | addr, 8)
        for word in words:
            await self._shift(word, 32)
        await self._deselect()

    async def aborted_write(self, addr, data_bits):
        """Raises CS_n partway through a frame's data."""
        await self._select()
        await self._shift(0x80 | addr, 8)
        await self._shift(0, data_bits)
        await self._deselect()


class UartMonitor:
    """Independent UART receiver on uo_out[0], checking every clk of every bit."""

    def __init__(self, dut):
        self.dut = dut
        self.received = []
        cocotb.start_soon(self._run())

    def _line(self):
        return self.dut.uo_out.value.to_unsigned() & 1

    async def _sample(self):
        await FallingEdge(self.dut.clk)
        return self._line()

    async def _run(self):
        previous = await self._sample()
        while True:
            level = await self._sample()
            if previous == 1 and level == 0:
                level = await self._receive_frame()
            previous = level

    async def _receive_frame(self):
        # The falling edge we just saw is the first sample of the start bit
        samples = [0]
        for _ in range(FRAME_BITS * CYCLES_PER_BIT - 1):
            samples.append(await self._sample())
        cells = [samples[i:i + CYCLES_PER_BIT] for i in range(0, len(samples), CYCLES_PER_BIT)]

        for index, cell in enumerate(cells):
            assert len(set(cell)) == 1, f"bit {index} of frame changed mid-bit: {cell}"
        assert cells[0][0] == 0, "start bit not low"
        assert cells[9][0] == 1, f"stop bit not high in frame {len(self.received)}"

        self.received.append(sum(cells[1 + i][0] << i for i in range(8)))  # LSB first
        return 1

    async def wait_for(self, count, timeout_cycles=None):
        """Waits for `count` frames in total, then checks the line went idle."""
        if timeout_cycles is None:
            timeout_cycles = (count + 2) * FRAME_BITS * CYCLES_PER_BIT * 4
        for _ in range(timeout_cycles):
            if len(self.received) >= count:
                break
            await FallingEdge(self.dut.clk)
        assert len(self.received) >= count, f"got {len(self.received)} frames, expected {count}"
        assert self._line() == 1, "line not idle after frames"


def program_words():
    return [int(word, 16) for word in PROGRAM_HEX.read_text().split()]


async def reset_chip(dut, half_period=5):
    cocotb.start_soon(Clock(dut.clk, 10, unit="us").start())
    host = HostSpi(dut, half_period)
    dut.ena.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return host


async def load_program(host):
    # One frame per word: host_regs doesn't auto-increment the address yet
    for offset, word in enumerate(program_words()):
        await host.write(ADDR_INSTR + offset, word)


async def start_uart_tx(dut, half_period=5):
    host = await reset_chip(dut, half_period)
    await load_program(host)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 20)
    monitor = UartMonitor(dut)
    return host, monitor


@cocotb.test()
async def test_line_idles_high_when_enabled(dut):
    host, monitor = await start_uart_tx(dut)
    for _ in range(FRAME_BITS * CYCLES_PER_BIT * 3):
        await FallingEdge(dut.clk)
        assert dut.uo_out.value.to_unsigned() & 1 == 1, "line left idle with nothing to send"
    assert monitor.received == []


@cocotb.test()
async def test_single_byte(dut):
    host, monitor = await start_uart_tx(dut)
    await host.write(ADDR_SM0_TXF, ord("A"))
    await monitor.wait_for(1)
    assert monitor.received == [ord("A")]


@cocotb.test()
async def test_every_byte_value(dut):
    host, monitor = await start_uart_tx(dut)
    for value in range(256):
        await host.write(ADDR_SM0_TXF, value)
    await monitor.wait_for(256)
    assert monitor.received == list(range(256))


@cocotb.test()
async def test_burst_at_max_spi_speed(dut):
    """Burst at SCK = clk/4: frames must come out in order, one stop bit apart."""
    host, monitor = await start_uart_tx(dut, half_period=2)
    message = b"Hello, PIO!"
    await host.write(ADDR_SM0_TXF, *message)
    await monitor.wait_for(len(message))
    assert bytes(monitor.received) == message


@cocotb.test()
async def test_random_bytes_gaps_and_spi_speeds(dut):
    host, monitor = await start_uart_tx(dut)
    expected = []
    for _ in range(100):
        host.half_period = random.choice([2, 3, 5, 10])
        value = random.randrange(256)
        await host.write(ADDR_SM0_TXF, value)
        expected.append(value)
        await ClockCycles(dut.clk, random.randrange(1, 300))
    await monitor.wait_for(len(expected))
    assert monitor.received == expected


@cocotb.test()
async def test_aborted_frame_sends_nothing(dut):
    host, monitor = await start_uart_tx(dut)
    for bits_sent in (0, 1, 16, 31):
        await host.aborted_write(ADDR_SM0_TXF, bits_sent)
    await ClockCycles(dut.clk, FRAME_BITS * CYCLES_PER_BIT * 2)
    assert monitor.received == [], "an aborted frame pushed a word"

    await host.write(ADDR_SM0_TXF, 0x5A)
    await monitor.wait_for(1)
    assert monitor.received == [0x5A]


@cocotb.test()
async def test_nothing_sent_until_enabled(dut):
    host = await reset_chip(dut)
    await load_program(host)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x42)
    await ClockCycles(dut.clk, FRAME_BITS * CYCLES_PER_BIT * 2)
    assert monitor.received == [], "a disabled state machine transmitted"

    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await monitor.wait_for(1)
    assert monitor.received == [0x42]


@cocotb.test()
async def test_program_really_comes_from_spi(dut):
    """Blank the memory over SPI (no UART), then load the program (UART works)."""
    host = await reset_chip(dut)
    for offset in range(32):
        await host.write(ADDR_INSTR + offset, 0x0000)  # 0x0000 = jmp 0
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x33)
    await ClockCycles(dut.clk, FRAME_BITS * CYCLES_PER_BIT * 2)
    assert monitor.received == [], "transmitted with blank instruction memory"

    # Reset clears the state machine and FIFO but not the memory
    host = await reset_chip(dut)
    await load_program(host)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 20)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x7E)
    await monitor.wait_for(1)
    assert monitor.received == [0x7E]
