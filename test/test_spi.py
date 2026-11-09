# SPDX-License-Identifier: Apache-2.0
"""PIO as an SPI controller, plus how the PIO pins reach uio.

The program is the datasheet's spi_cpha0 (3.6.1). The SPI device is modelled at the
pins: SCK = uio[0], MOSI = uio[1], MISO = uio[2].
"""

import random

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from test_uart_tx import ADDR_CTRL, ADDR_INSTR, reset_chip

ADDR_FSTAT = 0x21
ADDR_TXF = 0x30
ADDR_RXF = 0x31
SM0_REGS = 0x30
SM1_REGS = 0x40
REG_CLKDIV = 2
REG_EXECCTRL = 3
REG_SHIFTCTRL = 4
REG_PINCTRL = 5
REG_START_PC = 6
REG_PINS_INIT = 8
REG_PINDIRS_INIT = 9
CTRL_SM0_ENABLE = 0x1
FSTAT_SM0_RX_EMPTY = 1 << 8
FSTAT_SM0_RX_FULL = 1 << 0

PIN_SCK = 0
PIN_MOSI = 1
PIN_MISO = 2

MODE_0_PROGRAM = (
    0x6101,  # out pins, 1 side 0 [1]  ; stalls here on an empty FIFO, with SCK low
    0x5101,  # in  pins, 1 side 1 [1]
)
MODE_2_PROGRAM = (
    0x7101,  # out pins, 1 side 1 [1]  ; the same with SCK idling high (CPOL=1)
    0x4101,  # in  pins, 1 side 0 [1]
)

EXECCTRL_WRAP_0_TO_1 = 1 << 12
# One side-set pin at SCK, one OUT pin at MOSI, IN base at MISO
PINCTRL = (1 << 29) | (1 << 20) | (PIN_MISO << 15) | (PIN_SCK << 10) | PIN_MOSI
HALF_PERIOD = 4  # host SCK = clk/8
TICKS_PER_BIT = 4  # the program spends 2 ticks in each half of SCK


def shiftctrl(frame_bits):
    """Shift left (MSB first), autopull and autopush on. A threshold of 32 is encoded as 0."""
    threshold = frame_bits & 31
    return (threshold << 25) | (threshold << 20) | (1 << 17) | (1 << 16)


class SpiDevice:
    """An SPI device on the pins: samples MOSI on the leading SCK edge, changes MISO on the trailing one."""

    def __init__(self, dut, replies=(), frame_bits=8, cpol=0, output_delay=1):
        self.dut = dut
        self.replies = list(replies)
        self.frame_bits = frame_bits
        self.cpol = cpol
        self.output_delay = output_delay  # clk cycles from the trailing edge to MISO changing
        self.received = []
        self.leading_edges = []  # clk cycle of every leading SCK edge
        self.outgoing = self._next_reply_bits()
        self._drive_miso(self.outgoing.pop(0))  # a mode 0 device presents its first bit up front
        self.task = cocotb.start_soon(self._run())

    def _next_reply_bits(self):
        value = self.replies.pop(0) if self.replies else 0
        return [(value >> i) & 1 for i in range(self.frame_bits - 1, -1, -1)]

    def _drive_miso(self, level):
        self.dut.uio_in.value = level << PIN_MISO

    def _pin(self, number):
        return (self.dut.uio_out.value.to_unsigned() >> number) & 1

    def sck(self):
        return self._pin(PIN_SCK)

    async def _run(self):
        previous = self.cpol
        shift_in = []
        pending = []  # (clk cycle to apply it on, MISO level)
        cycle = 0
        while True:
            await FallingEdge(self.dut.clk)
            cycle += 1
            level = self.sck()
            if level != previous:
                if previous == self.cpol:  # leading edge
                    self.leading_edges.append(cycle)
                    shift_in.append(self._pin(PIN_MOSI))
                    if len(shift_in) == self.frame_bits:
                        self.received.append(int("".join(map(str, shift_in)), 2))
                        shift_in = []
                else:  # trailing edge: present the next bit
                    if not self.outgoing:
                        self.outgoing = self._next_reply_bits()
                    pending.append((cycle + self.output_delay, self.outgoing.pop(0)))
                previous = level
            while pending and pending[0][0] <= cycle:
                self._drive_miso(pending.pop(0)[1])


async def start_spi(
    dut, replies=(), frame_bits=8, clkdiv=4, output_delay=1, program=MODE_0_PROGRAM, cpol=0, start_clock=True, enable=True
):
    host = await reset_chip(dut, HALF_PERIOD, start_clock=start_clock)
    for offset, word in enumerate(program):
        await host.write(ADDR_INSTR + offset, word)
    await host.write(SM0_REGS + REG_CLKDIV, clkdiv << 16)
    await host.write(SM0_REGS + REG_EXECCTRL, EXECCTRL_WRAP_0_TO_1)
    await host.write(SM0_REGS + REG_SHIFTCTRL, shiftctrl(frame_bits))
    await host.write(SM0_REGS + REG_PINCTRL, PINCTRL)
    await host.write(SM0_REGS + REG_START_PC, 0)
    await host.write(SM0_REGS + REG_PINS_INIT, cpol << PIN_SCK)
    await host.write(SM0_REGS + REG_PINDIRS_INIT, (1 << PIN_SCK) | (1 << PIN_MOSI))
    await ClockCycles(dut.clk, 5)
    device = SpiDevice(dut, replies, frame_bits, cpol, output_delay)
    if enable:
        await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    return host, device


async def send(host, value, frame_bits=8):
    await host.write(ADDR_TXF, value << (32 - frame_bits))  # MSB first, so left-justified


async def read_reply(host, frame_bits=8, timeout_polls=500):
    for _ in range(timeout_polls):
        if not await host.read(ADDR_FSTAT) & FSTAT_SM0_RX_EMPTY:
            word = await host.read(ADDR_RXF)
            mask = (1 << frame_bits) - 1
            assert word & ~mask == 0, f"bits above the frame are set: {word:#010x}"
            return word
    assert False, "no reply reached the RX FIFO"


@cocotb.test()
@cocotb.parametrize(clkdiv=[2, 3, 4, 8])
async def test_full_duplex_byte(dut, clkdiv):
    host, device = await start_spi(dut, replies=[0xA5], clkdiv=clkdiv)
    await send(host, 0x3C)
    assert await read_reply(host) == 0xA5
    assert device.received == [0x3C]


@cocotb.test()
@cocotb.parametrize(clkdiv=[2, 3, 4, 8])
async def test_slowest_device_each_divider_allows(dut, clkdiv):
    """sync2 leaves a device 2*clkdiv-2 clk cycles to answer, less the half cycle the model uses."""
    host, device = await start_spi(dut, replies=[0xA5], clkdiv=clkdiv, output_delay=2 * clkdiv - 3)
    await send(host, 0x5A)
    assert await read_reply(host) == 0xA5
    assert device.received == [0x5A]


@cocotb.test()
async def test_clkdiv_1_reads_stale_miso(dut):
    """A known limit: at clkdiv=1, sync2's delay makes IN see MISO from before the last SCK fall."""
    host, device = await start_spi(dut, replies=[0xA5], clkdiv=1, output_delay=0)
    await send(host, 0x3C)
    # 0xA5 sampled one bit late: bit 7 is read twice and bit 0 is never read
    assert await read_reply(host) == 0xD2
    assert device.received == [0x3C], "MOSI doesn't go through sync2, so it should still be right"


@cocotb.test()
@cocotb.parametrize(clkdiv=[1, 2, 5])
async def test_sck_period_and_pulse_count(dut, clkdiv):
    host, device = await start_spi(dut, clkdiv=clkdiv)
    await send(host, 0xFF)
    await ClockCycles(dut.clk, 40 * clkdiv + 500)
    assert len(device.leading_edges) == 8, "wrong number of SCK pulses for one byte"
    gaps = {b - a for a, b in zip(device.leading_edges, device.leading_edges[1:])}
    assert gaps == {TICKS_PER_BIT * clkdiv}, f"SCK period wrong or uneven: {gaps}"


@cocotb.test()
async def test_pin_directions(dut):
    host, device = await start_spi(dut)
    oe = dut.uio_oe.value.to_unsigned()
    assert (oe >> PIN_SCK) & 1 and (oe >> PIN_MOSI) & 1, "SCK and MOSI aren't being driven"
    assert not (oe >> PIN_MISO) & 1, "the chip is driving MISO against the device"


@cocotb.test()
async def test_waits_with_sck_low_until_data_arrives(dut):
    host, device = await start_spi(dut, replies=[0x11, 0x22])
    await ClockCycles(dut.clk, 300)
    assert device.leading_edges == [], "SCK ran with an empty TX FIFO"
    assert device.sck() == 0

    await send(host, 0xAA)
    assert await read_reply(host) == 0x11
    await ClockCycles(dut.clk, 300)
    assert len(device.leading_edges) == 8, "SCK kept running after the FIFO emptied"
    assert device.sck() == 0, "SCK didn't stop low"

    await send(host, 0xBB)
    assert await read_reply(host) == 0x22
    assert device.received == [0xAA, 0xBB]


@cocotb.test()
async def test_queued_bytes_run_without_a_gap(dut):
    """Autopull refills in time, so bytes queued in the FIFO keep SCK perfectly even."""
    message = [0xDE, 0xAD, 0xBE, 0xEF]
    replies = [0x12, 0x34, 0x56, 0x78]
    host, device = await start_spi(dut, replies=list(replies), enable=False)
    for value in message:
        await send(host, value)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)

    assert [await read_reply(host) for _ in message] == replies
    assert device.received == message
    gaps = {b - a for a, b in zip(device.leading_edges, device.leading_edges[1:])}
    assert gaps == {TICKS_PER_BIT * 4}, f"gap between bytes: {gaps}"


@cocotb.test()
async def test_every_byte_value(dut):
    values = list(range(256))
    host, device = await start_spi(dut, replies=[v ^ 0xFF for v in values])
    for value in values:
        await send(host, value)
        assert await read_reply(host) == value ^ 0xFF
    assert device.received == values


@cocotb.test()
async def test_random_bytes_and_gaps(dut):
    sent = [random.randrange(256) for _ in range(60)]
    replies = [random.randrange(256) for _ in sent]
    host, device = await start_spi(dut, replies=list(replies))
    for value, reply in zip(sent, replies):
        await send(host, value)
        assert await read_reply(host) == reply
        await ClockCycles(dut.clk, random.randrange(1, 200))
    assert device.received == sent


@cocotb.test()
@cocotb.parametrize(frame_bits=[4, 12, 16, 32])
async def test_frame_sizes(dut, frame_bits):
    """The frame size comes only from the thresholds; the program doesn't change."""
    pattern = 0xA5C3F00F >> (32 - frame_bits)
    reply = 0x5A3C0FF0 >> (32 - frame_bits)
    host, device = await start_spi(dut, replies=[reply], frame_bits=frame_bits)
    await send(host, pattern, frame_bits)
    assert await read_reply(host, frame_bits) == reply
    assert device.received == [pattern]


@cocotb.test()
async def test_full_rx_fifo_pauses_transfer_without_losing_data(dut):
    sent = [0x10, 0x20, 0x30, 0x40, 0x50, 0x60]
    replies = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]
    host, device = await start_spi(dut, replies=list(replies))
    for value in sent:
        await send(host, value)
    await ClockCycles(dut.clk, 1000)

    assert await host.read(ADDR_FSTAT) & FSTAT_SM0_RX_FULL, "RX FIFO should have filled"
    assert len(device.received) == 5, "SCK should stop on the 5th byte until the MCU reads"

    assert [await read_reply(host) for _ in replies] == replies
    assert device.received == sent


@cocotb.test()
async def test_cpol_1_by_swapping_side_set(dut):
    """Mode 2 needs no hardware: the same program with the SCK levels swapped."""
    host, device = await start_spi(dut, replies=[0xA5], program=MODE_2_PROGRAM, cpol=1)
    await ClockCycles(dut.clk, 50)
    assert device.sck() == 1, "SCK should idle high"

    await send(host, 0x3C)
    assert await read_reply(host) == 0xA5
    assert device.received == [0x3C]
    await ClockCycles(dut.clk, 100)
    assert device.sck() == 1, "SCK should return to idling high"


# ---- How the PIO pins reach uio ----


async def set_pins(host, regs, level, direction):
    await host.write(regs + REG_PINS_INIT, level)
    await host.write(regs + REG_PINDIRS_INIT, direction)


def uio_state(dut):
    return dut.uio_out.value.to_unsigned(), dut.uio_oe.value.to_unsigned()


@cocotb.test()
async def test_pins_are_inputs_until_the_host_sets_directions(dut):
    host = await reset_chip(dut, HALF_PERIOD)
    assert uio_state(dut)[1] == 0, "a pin is driven straight after reset"
    await set_pins(host, SM0_REGS, 0x80, 0x88)
    await ClockCycles(dut.clk, 5)
    out, oe = uio_state(dut)
    assert oe == 0x88, "uio_oe doesn't follow PINDIRS_INIT"
    assert out == 0x80, "uio_out doesn't follow PINS_INIT"


@cocotb.test()
async def test_either_sm_can_drive_a_pin(dut):
    host = await reset_chip(dut, HALF_PERIOD)
    await set_pins(host, SM0_REGS, 0x01, 0x01)
    await set_pins(host, SM1_REGS, 0x02, 0x02)
    await ClockCycles(dut.clk, 5)
    out, oe = uio_state(dut)
    assert oe == 0x03
    assert out == 0x03


@cocotb.test()
async def test_higher_numbered_sm_wins_a_shared_pin(dut):
    host = await reset_chip(dut, HALF_PERIOD)
    await set_pins(host, SM0_REGS, 0x08, 0x08)
    await set_pins(host, SM1_REGS, 0x00, 0x08)
    await ClockCycles(dut.clk, 5)
    assert uio_state(dut) == (0x00, 0x08), "SM1 should win with a low"

    await set_pins(host, SM0_REGS, 0x00, 0x08)
    await set_pins(host, SM1_REGS, 0x08, 0x08)
    await ClockCycles(dut.clk, 5)
    assert uio_state(dut) == (0x08, 0x08), "SM1 should win with a high"


@cocotb.test()
async def test_a_pin_nobody_drives_ignores_its_value(dut):
    host = await reset_chip(dut, HALF_PERIOD)
    await set_pins(host, SM0_REGS, 0xFF, 0x00)
    await ClockCycles(dut.clk, 5)
    assert uio_state(dut) == (0x00, 0x00)


@cocotb.test()
async def test_directions_reload_when_disabled(dut):
    # The program turns pin 1 off, and disabling the SM must restore PINDIRS_INIT
    host, device = await start_spi(dut, program=(0xE080, 0x0001), enable=False)  # set pindirs, 0 ; jmp self
    await host.write(SM0_REGS + REG_PINCTRL, PINCTRL | (2 << 26))  # SET covers pins 0 and 1
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 40)
    assert uio_state(dut)[1] == 0x00, "SET PINDIRS didn't take effect"

    await host.write(ADDR_CTRL, 0)
    await ClockCycles(dut.clk, 5)
    assert uio_state(dut)[1] == 0x03, "directions didn't reload from PINDIRS_INIT"
