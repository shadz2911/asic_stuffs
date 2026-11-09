# SPDX-License-Identifier: Apache-2.0
"""End-to-end UART TX: SPI on the host pins in, UART frames on uio[0] out.

Everything is driven and judged at the chip's pins, so these tests never look
inside the design. Pins: ui_in[7] SCK, ui_in[6] CS_n, ui_in[5] MOSI, uio[0] TX.
"""

import random
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

ADDR_INSTR = 0x00
ADDR_CTRL = 0x20
ADDR_SM0_TXF = 0x30
ADDR_SM0_CLKDIV = 0x32
ADDR_SM0_EXECCTRL = 0x33
ADDR_SM0_SHIFTCTRL = 0x34
ADDR_SM0_PINCTRL = 0x35
ADDR_SM0_START_PC = 0x36
ADDR_SM0_PINS_INIT = 0x38
ADDR_SM0_PINDIRS_INIT = 0x39
CTRL_SM0_ENABLE = 0x1

UART_TX_PIN = 0  # uio[0]
UART_RX_PIN = 1  # uio[1]

# uart_tx config: side_set 1 opt, wrap 0-3, shift right, side-set and OUT on pin 0
UART_TX_EXECCTRL = 0x40003000
UART_TX_SHIFTCTRL = 0x000C0000
UART_TX_PINCTRL = 0x40100000

TICKS_PER_BIT = 8  # set by the uart_tx program
FRAME_BITS = 10  # start + 8 data + stop

PROGRAM_HEX = Path(__file__).parent / "build" / "uart.hex"


def tx_line(dut):
    """The level on the wire: a pin the chip isn't driving is held high by the board's pull-up."""
    if not (dut.uio_oe.value.to_unsigned() >> UART_TX_PIN) & 1:
        return 1
    return (dut.uio_out.value.to_unsigned() >> UART_TX_PIN) & 1


class HostSpi:
    """Models the MCU: SPI mode 0, MSB first, one 40-bit frame per write."""

    def __init__(self, dut, half_period=5):
        self.dut = dut
        self.half_period = half_period  # clk cycles per SCK phase; 2 is the clk/4 limit
        self._drive(sck=0, cs_n=1, mosi=0)

    def _drive(self, sck, cs_n, mosi):
        self.dut.ui_in.value = (sck << 7) | (cs_n << 6) | (mosi << 5)

    def set_rx_line(self, level):
        self.dut.uio_in.value = level << UART_RX_PIN

    async def _wait(self, cycles=None):
        await ClockCycles(self.dut.clk, self.half_period if cycles is None else cycles, rising=False)

    async def _shift(self, value, nbits):
        """Sends nbits on MOSI and returns the bits sampled from MISO on each rising edge."""
        received = 0
        for i in range(nbits - 1, -1, -1):
            bit = (value >> i) & 1
            self._drive(sck=0, cs_n=0, mosi=bit)
            await self._wait()
            received = (received << 1) | (self.dut.uo_out.value.to_unsigned() >> 7)
            self._drive(sck=1, cs_n=0, mosi=bit)
            await self._wait()
        self._drive(sck=0, cs_n=0, mosi=0)
        return received

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

    async def read(self, addr, count=1):
        """One read frame: command, dummy byte, then `count` words (a burst if more than 1)."""
        await self._select()
        await self._shift(addr, 8)
        await self._shift(0, 8)
        words = [await self._shift(0, 32) for _ in range(count)]
        await self._deselect()
        return words[0] if count == 1 else words

    async def aborted_read(self, addr, dummy_bits):
        """Raises CS_n partway through a read's dummy byte."""
        await self._select()
        await self._shift(addr, 8)
        await self._shift(0, dummy_bits)
        await self._deselect()

    async def aborted_write(self, addr, data_bits):
        """Raises CS_n partway through a frame's data."""
        await self._select()
        await self._shift(0x80 | addr, 8)
        await self._shift(0, data_bits)
        await self._deselect()


class UartMonitor:
    """Independent UART receiver on uio[0], checking every clk of every bit."""

    def __init__(self, dut, clkdiv=1):
        self.dut = dut
        self.cycles_per_bit = TICKS_PER_BIT * clkdiv
        self.received = []
        cocotb.start_soon(self._run())

    def _line(self):
        return tx_line(self.dut)

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
        for _ in range(FRAME_BITS * self.cycles_per_bit - 1):
            samples.append(await self._sample())
        cells = [samples[i:i + self.cycles_per_bit] for i in range(0, len(samples), self.cycles_per_bit)]

        for index, cell in enumerate(cells):
            assert len(set(cell)) == 1, f"bit {index} of frame changed mid-bit: {cell}"
        assert cells[0][0] == 0, "start bit not low"
        assert cells[9][0] == 1, f"stop bit not high in frame {len(self.received)}"

        self.received.append(sum(cells[1 + i][0] << i for i in range(8)))  # LSB first
        return 1

    async def wait_for(self, count, timeout_cycles=None):
        """Waits for `count` frames in total, then checks the line went idle."""
        if timeout_cycles is None:
            timeout_cycles = (count + 2) * FRAME_BITS * self.cycles_per_bit * 4
        for _ in range(timeout_cycles):
            if len(self.received) >= count:
                break
            await FallingEdge(self.dut.clk)
        assert len(self.received) >= count, f"got {len(self.received)} frames, expected {count}"
        assert self._line() == 1, "line not idle after frames"


def program_words():
    return [int(word, 16) for word in PROGRAM_HEX.read_text().split()]


async def reset_chip(dut, half_period=5, start_clock=True):
    if start_clock:
        cocotb.start_soon(Clock(dut.clk, 10, unit="us").start())
    host = HostSpi(dut, half_period)
    dut.ena.value = 1
    host.set_rx_line(1)  # the UART RX line idles high
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    return host


async def load_program(host):
    # One frame per word: host_regs doesn't auto-increment the address yet
    for offset, word in enumerate(program_words()):
        await host.write(ADDR_INSTR + offset, word)


async def configure_uart_tx(host, clkdiv=1, execctrl=UART_TX_EXECCTRL, start_pc=0):
    await host.write(ADDR_SM0_CLKDIV, clkdiv << 16)
    await host.write(ADDR_SM0_EXECCTRL, execctrl)
    await host.write(ADDR_SM0_SHIFTCTRL, UART_TX_SHIFTCTRL)
    await host.write(ADDR_SM0_PINCTRL, UART_TX_PINCTRL)
    await host.write(ADDR_SM0_START_PC, start_pc)
    await host.write(ADDR_SM0_PINDIRS_INIT, 1 << UART_TX_PIN)


async def start_uart_tx(dut, half_period=5, clkdiv=1):
    host = await reset_chip(dut, half_period)
    await load_program(host)
    await configure_uart_tx(host, clkdiv)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 20 * clkdiv)
    monitor = UartMonitor(dut, clkdiv)
    return host, monitor


@cocotb.test()
async def test_line_never_drops_from_reset_to_enable(dut):
    """A low TX line looks like a start bit to a receiver, so it must stay high throughout."""
    host = await reset_chip(dut)

    async def watch_line():
        while True:
            await FallingEdge(dut.clk)
            assert tx_line(dut) == 1, "TX line dropped before any byte was sent"

    watcher = cocotb.start_soon(watch_line())
    await load_program(host)
    await configure_uart_tx(host)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, FRAME_BITS * TICKS_PER_BIT * 2)
    watcher.cancel()


@cocotb.test()
async def test_host_sets_pin_level_while_disabled(dut):
    host = await reset_chip(dut)
    await host.write(ADDR_SM0_PINDIRS_INIT, 1 << UART_TX_PIN)
    await host.write(ADDR_SM0_PINS_INIT, 0x00)
    await ClockCycles(dut.clk, 5)
    assert tx_line(dut) == 0, "line ignored a start value of 0"

    await host.write(ADDR_SM0_PINS_INIT, 0x01)
    await ClockCycles(dut.clk, 5)
    assert tx_line(dut) == 1, "line ignored a start value of 1"


@cocotb.test()
async def test_line_idles_high_when_enabled(dut):
    host, monitor = await start_uart_tx(dut)
    for _ in range(FRAME_BITS * TICKS_PER_BIT * 3):
        await FallingEdge(dut.clk)
        assert tx_line(dut) == 1, "line left idle with nothing to send"
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
    await ClockCycles(dut.clk, FRAME_BITS * TICKS_PER_BIT * 2)
    assert monitor.received == [], "an aborted frame pushed a word"

    await host.write(ADDR_SM0_TXF, 0x5A)
    await monitor.wait_for(1)
    assert monitor.received == [0x5A]


@cocotb.test()
async def test_nothing_sent_until_enabled(dut):
    host = await reset_chip(dut)
    await load_program(host)
    await configure_uart_tx(host)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x42)
    await ClockCycles(dut.clk, FRAME_BITS * TICKS_PER_BIT * 2)
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
    await configure_uart_tx(host)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x33)
    await ClockCycles(dut.clk, FRAME_BITS * TICKS_PER_BIT * 2)
    assert monitor.received == [], "transmitted with blank instruction memory"

    # Reset clears the state machine and FIFO but not the memory
    host = await reset_chip(dut, start_clock=False)
    await load_program(host)
    await configure_uart_tx(host)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 20)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0x7E)
    await monitor.wait_for(1)
    assert monitor.received == [0x7E]


@cocotb.test()
@cocotb.parametrize(clkdiv=[2, 3, 54])
async def test_clock_divider_sets_bit_time(dut, clkdiv):
    host, monitor = await start_uart_tx(dut, clkdiv=clkdiv)
    await host.write(ADDR_SM0_TXF, 0xA5)
    await monitor.wait_for(1)
    assert monitor.received == [0xA5]


@cocotb.test()
async def test_reconfigure_while_disabled(dut):
    host, monitor = await start_uart_tx(dut, clkdiv=2)
    await host.write(ADDR_SM0_TXF, 0x11)
    await monitor.wait_for(1)

    await host.write(ADDR_CTRL, 0)
    await configure_uart_tx(host, clkdiv=5)
    monitor.cycles_per_bit = TICKS_PER_BIT * 5
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await host.write(ADDR_SM0_TXF, 0x22)
    await monitor.wait_for(2)
    assert monitor.received == [0x11, 0x22]


@cocotb.test()
async def test_program_at_another_address(dut):
    """uart_tx relocated to word 8: needs start_pc, wrap and the JMP target to agree."""
    host = await reset_chip(dut)
    for offset in range(8):
        await host.write(ADDR_INSTR + offset, 0x0000)  # 0x0000 = jmp 0, so a wrong start_pc sends nothing
    for offset, word in enumerate((0x9FA0, 0xF727, 0x6001, 0x064A)):
        await host.write(ADDR_INSTR + 8 + offset, word)
    await configure_uart_tx(host, execctrl=0x4000B400, start_pc=8)  # wrap 8-11
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 20)
    monitor = UartMonitor(dut)
    await host.write(ADDR_SM0_TXF, 0xC3)
    await monitor.wait_for(1)
    assert monitor.received == [0xC3]
