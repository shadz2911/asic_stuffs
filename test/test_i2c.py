# SPDX-License-Identifier: Apache-2.0
"""PIO as an I2C controller, running the unmodified pico-examples i2c program.

The program and the Start/Stop table come from programs/i2c.pio (datasheet 3.6.7).
The MCU side follows pico-examples pio_i2c.c, with one change: autopush stays on and
writes drain the RX FIFO, because the SDK's `mov isr, null` needs MOV, which isn't
implemented yet. The I2C device is modelled on an open-drain bus with pull-ups.
"""

import random
import re
from pathlib import Path

import adafruit_pioasm
import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from test_uart_tx import ADDR_CTRL, ADDR_INSTR, reset_chip

ADDR_FSTAT = 0x21
ADDR_IRQ = 0x22
ADDR_OE_INVERT = 0x23
ADDR_TXF = 0x30
ADDR_RXF = 0x31
ADDR_SM0_INSTR = 0x3A
SM0_REGS = 0x30
REG_CLKDIV = 2
REG_EXECCTRL = 3
REG_SHIFTCTRL = 4
REG_PINCTRL = 5
REG_START_PC = 6
REG_PINS_INIT = 8
REG_PINDIRS_INIT = 9
CTRL_SM0_ENABLE = 0x1
FSTAT_SM0_TX_EMPTY = 1 << 24
FSTAT_SM0_TX_FULL = 1 << 16
FSTAT_SM0_RX_EMPTY = 1 << 8
IRQ_SM0 = 1 << 0  # `irq wait 0 rel` on SM0 uses flag 0

PIN_SDA = 2
PIN_SCL = 3  # the program needs SCL = SDA + 1
HALF_PERIOD = 4
DEVICE_ADDRESS = 0x50

ICOUNT_LSB = 10
FINAL_LSB = 9
MOV_ISR_NULL = 0xA0C3  # mov isr, null: a no-op here until MOV exists
OUT_NULL_32 = 0x6060  # what pio_sm_drain_tx_fifo forces when autopull is on
SC0_SD0, SC0_SD1, SC1_SD0, SC1_SD1 = range(4)
CYCLES_PER_BIT = 32  # program ticks per SCL period


def assemble_programs():
    """Assembles each .program in programs/i2c.pio on its own (the assembler takes one at a time)."""
    text = (Path(__file__).parent.parent / "programs" / "i2c.pio").read_text()
    text = re.sub(r"%\s*c-sdk\s*\{.*?%\}", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*\.pio_version.*$", "", text)
    programs = {}
    for part in re.split(r"(?m)^(?=\.program\s)", text):
        if part.lstrip().startswith(".program"):
            programs[part.split()[1]] = adafruit_pioasm.Program(part)
    return programs


PROGRAMS = assemble_programs()
I2C = PROGRAMS["i2c"]
TABLE = list(PROGRAMS["set_scl_sda"].assembled)
ENTRY = I2C.public_labels["entry_point"]
WRAP_BOTTOM = I2C.pio_kwargs["wrap_target"]
WRAP_TOP = I2C.pio_kwargs["wrap"]


class I2cDevice:
    """An I2C target with 256 registers, on an open-drain bus with pull-ups.

    A write's first data byte sets the register pointer; later bytes are stored and
    the pointer increments. Reads return registers from the pointer onwards.
    """

    def __init__(self, dut, address=DEVICE_ADDRESS, stretch=0, nak_data_after=None):
        self.dut = dut
        self.address = address
        self.stretch = stretch  # clk cycles to hold SCL low after each ACK
        self.nak_data_after = nak_data_after  # NAK this many data bytes into a write
        self.mem = [0] * 256
        self.pointer = 0
        self.events = []  # "S" start, "Sr" repeated start, "P" stop, ("addr", a, rw, acked)
        self.writes = []  # data bytes of each completed write
        self.scl_rises = []
        self.cycle = 0
        self.state = "idle"
        self.sda_low = False
        self.scl_low = False
        self.stretch_left = 0
        self.task = cocotb.start_soon(self._run())

    def _chip_pulls_low(self, pin):
        oe = self.dut.uio_oe.value.to_unsigned()
        out = self.dut.uio_out.value.to_unsigned()
        return (oe >> pin) & 1 and not (out >> pin) & 1

    def lines(self):
        sda = 0 if self._chip_pulls_low(PIN_SDA) or self.sda_low else 1
        scl = 0 if self._chip_pulls_low(PIN_SCL) or self.scl_low else 1
        return sda, scl

    def _drive_inputs(self):
        sda, scl = self.lines()
        self.dut.uio_in.value = (sda << PIN_SDA) | (scl << PIN_SCL) | 0x02  # bit 1: idle UART RX

    async def _run(self):
        self._drive_inputs()
        prev_sda, prev_scl = self.lines()
        while True:
            await FallingEdge(self.dut.clk)
            self.cycle += 1
            if self.stretch_left:
                self.stretch_left -= 1
                self.scl_low = self.stretch_left > 0
            sda, scl = self.lines()
            if scl and prev_scl and sda != prev_sda:
                self._start() if sda == 0 else self._stop()
            elif scl and not prev_scl:
                self.scl_rises.append(self.cycle)
                self._rise(sda)
            elif prev_scl and not scl:
                self._fall()
            self._drive_inputs()
            prev_sda, prev_scl = self.lines()

    def _finish_write(self):
        if self.state == "write":
            self.writes.append(self.current)

    def _start(self):
        self._finish_write()
        self.events.append("S" if self.state == "idle" else "Sr")
        self.state, self.bits, self.shift, self.sda_low = "addr", 0, 0, False

    def _stop(self):
        self._finish_write()
        self.events.append("P")
        self.state, self.sda_low = "idle", False

    def _rise(self, sda):
        if self.state in ("idle", "ignore"):
            return
        self.bits += 1
        if self.state in ("addr", "write") and self.bits <= 8:
            self.shift = (self.shift << 1) | sda
        elif self.state == "read" and self.bits == 9:
            self.master_acked = sda == 0

    def _drive_bit(self):
        self.sda_low = not (self.out_byte >> (7 - self.bits)) & 1

    def _next_read_byte(self):
        self.out_byte = self.mem[self.pointer]
        self.pointer = (self.pointer + 1) % 256
        self.bits = 0
        self._drive_bit()

    def _fall(self):
        if self.state in ("idle", "ignore"):
            return
        if self.state == "addr":
            if self.bits == 8:
                address, read = self.shift >> 1, self.shift & 1
                self.acked = address == self.address
                self.events.append(("addr", address, read, self.acked))
                self.next_state = "read" if read else "write"
                self.sda_low = self.acked
            elif self.bits == 9:
                self.sda_low = False
                if not self.acked:
                    self.state = "ignore"
                elif self.next_state == "write":
                    self.state, self.bits, self.shift, self.current = "write", 0, 0, []
                else:
                    self.state = "read"
                    self._next_read_byte()
                self._maybe_stretch()
        elif self.state == "write":
            if self.bits == 8:
                self.acked = self.nak_data_after is None or len(self.current) < self.nak_data_after
                if self.acked:
                    if not self.current:
                        self.pointer = self.shift
                    else:
                        self.mem[self.pointer] = self.shift
                        self.pointer = (self.pointer + 1) % 256
                    self.current.append(self.shift)
                self.sda_low = self.acked
            elif self.bits == 9:
                self.sda_low = False
                self.bits, self.shift = 0, 0
                if not self.acked:
                    self._finish_write()
                    self.state = "ignore"
                self._maybe_stretch()
        elif self.state == "read":
            if self.bits < 8:
                self._drive_bit()
            elif self.bits == 8:
                self.sda_low = False  # the controller drives ACK/NAK
            else:
                if self.master_acked:
                    self._next_read_byte()
                else:
                    self.sda_low = False
                    self.state = "ignore"
                self._maybe_stretch()

    def _maybe_stretch(self):
        if self.stretch:
            self.scl_low = True
            self.stretch_left = self.stretch


class I2cController:
    """The MCU's side, ported from pico-examples pio_i2c.c, talking to the chip over SPI."""

    def __init__(self, dut, host, clkdiv):
        self.dut = dut
        self.host = host
        self.clkdiv = clkdiv

    async def fstat(self):
        return await self.host.read(ADDR_FSTAT)

    async def check_error(self):
        return bool(await self.host.read(ADDR_IRQ) & IRQ_SM0)

    async def put16(self, data):
        while await self.fstat() & FSTAT_SM0_TX_FULL:
            pass
        await self.host.write(ADDR_TXF, data << 16)  # left-justified: the RP2040's halfword write

    async def put_or_err(self, data):
        while await self.fstat() & FSTAT_SM0_TX_FULL:
            if await self.check_error():
                return
        if await self.check_error():
            return
        await self.host.write(ADDR_TXF, data << 16)

    async def drain_rx(self):
        received = []
        while not await self.fstat() & FSTAT_SM0_RX_EMPTY:
            received.append(await self.host.read(ADDR_RXF))
        return received

    async def start(self):
        await self.put_or_err(2 << ICOUNT_LSB)
        await self.put_or_err(TABLE[SC1_SD0])
        await self.put_or_err(TABLE[SC0_SD0])
        await self.put_or_err(MOV_ISR_NULL)

    async def stop(self):
        await self.put_or_err(2 << ICOUNT_LSB)
        await self.put_or_err(TABLE[SC0_SD0])
        await self.put_or_err(TABLE[SC1_SD0])
        await self.put_or_err(TABLE[SC1_SD1])

    async def repstart(self):
        await self.put_or_err(4 << ICOUNT_LSB)
        await self.put_or_err(TABLE[SC0_SD1])
        await self.put_or_err(TABLE[SC1_SD1])
        await self.put_or_err(TABLE[SC1_SD0])
        await self.put_or_err(TABLE[SC0_SD0])
        await self.put_or_err(MOV_ISR_NULL)

    async def wait_idle(self, timeout_polls=400):
        """The RP2040 watches FDEBUG.TXSTALL; this waits for an empty TX FIFO, then for the last words to run."""
        for _ in range(timeout_polls):
            if await self.check_error():
                return
            if await self.fstat() & FSTAT_SM0_TX_EMPTY:
                break
        else:
            assert False, "TX FIFO never emptied"
        await ClockCycles(self.dut.clk, 3 * 8 * 2 * self.clkdiv + 100)

    async def resume_after_error(self):
        # pio_sm_drain_tx_fifo, then a forced jump to the wrap bottom, then clear the flag
        while not await self.fstat() & FSTAT_SM0_TX_EMPTY:
            await self.host.write(ADDR_SM0_INSTR, OUT_NULL_32)
        await self.host.write(ADDR_SM0_INSTR, WRAP_BOTTOM)  # jmp to wrap_bottom
        await self.host.write(ADDR_IRQ, IRQ_SM0)

    async def write_blocking(self, address, data):
        await self.start()
        await self.put16((address << 2) | 1)
        for index, byte in enumerate(data):
            if await self.check_error():
                break
            final = index == len(data) - 1
            await self.put_or_err((byte << 1) | (final << FINAL_LSB) | 1)
            await self.drain_rx()
        await self.stop()
        await self.wait_idle()
        await self.drain_rx()
        if await self.check_error():
            await self.resume_after_error()
            await self.stop()
            await self.wait_idle()
            return -1
        return 0

    async def read_blocking(self, address, length):
        await self.start()
        await self.drain_rx()
        await self.put16((address << 2) | 3)
        tx_remain = length
        first = True
        received = []
        while (tx_remain or len(received) < length) and not await self.check_error():
            if tx_remain and not await self.fstat() & FSTAT_SM0_TX_FULL:
                tx_remain -= 1
                last = (1 << FINAL_LSB) | 1 if tx_remain == 0 else 0
                await self.put16((0xFF << 1) | last)
            if not await self.fstat() & FSTAT_SM0_RX_EMPTY:
                word = await self.host.read(ADDR_RXF)
                if first:
                    first = False  # the address byte, sampled back off the bus
                else:
                    received.append(word & 0xFF)
        await self.stop()
        await self.wait_idle()
        await self.drain_rx()
        if await self.check_error():
            await self.resume_after_error()
            await self.stop()
            await self.wait_idle()
            return -1
        return received


async def start_i2c(dut, clkdiv=2, start_clock=True, **device_args):
    """Follows i2c_program_init: pins low, directions out, then OE inverted, so the bus stays released."""
    host = await reset_chip(dut, HALF_PERIOD, start_clock=start_clock)
    device = I2cDevice(dut, **device_args)
    for offset, word in enumerate(I2C.assembled):
        await host.write(ADDR_INSTR + offset, word)
    side_set_pindirs = (1 << 30) | (1 << 29)
    await host.write(SM0_REGS + REG_CLKDIV, clkdiv << 16)
    await host.write(SM0_REGS + REG_EXECCTRL, side_set_pindirs | (PIN_SDA << 24) | (WRAP_TOP << 12) | (WRAP_BOTTOM << 7))
    await host.write(SM0_REGS + REG_SHIFTCTRL, (16 << 25) | (8 << 20) | (1 << 17) | (1 << 16))
    pinctrl = (2 << 29) | (1 << 26) | (1 << 20) | (PIN_SDA << 15) | (PIN_SCL << 10) | (PIN_SDA << 5) | PIN_SDA
    await host.write(SM0_REGS + REG_PINCTRL, pinctrl)  # side-set count 2 = 1 pin + the opt enable bit
    await host.write(SM0_REGS + REG_START_PC, ENTRY)
    both = (1 << PIN_SDA) | (1 << PIN_SCL)
    await host.write(SM0_REGS + REG_PINS_INIT, 0)
    await host.write(SM0_REGS + REG_PINDIRS_INIT, both)
    await host.write(ADDR_OE_INVERT, both)
    await host.write(ADDR_CTRL, CTRL_SM0_ENABLE)
    await ClockCycles(dut.clk, 50)
    return I2cController(dut, host, clkdiv), device


def assert_bus_idle(device):
    assert device.lines() == (1, 1), f"bus not released: SDA, SCL = {device.lines()}"


@cocotb.test()
async def test_bus_idles_released(dut):
    controller, device = await start_i2c(dut)
    await ClockCycles(dut.clk, 500)
    assert_bus_idle(device)
    assert device.events == [], "something happened on the bus with nothing queued"


@cocotb.test()
async def test_write_bytes(dut):
    controller, device = await start_i2c(dut)
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x10, 0xDE, 0xAD, 0xBE, 0xEF]) == 0
    assert device.writes == [[0x10, 0xDE, 0xAD, 0xBE, 0xEF]]
    assert device.mem[0x10:0x14] == [0xDE, 0xAD, 0xBE, 0xEF]
    assert device.events == ["S", ("addr", DEVICE_ADDRESS, 0, True), "P"]
    assert_bus_idle(device)


@cocotb.test()
async def test_read_bytes(dut):
    controller, device = await start_i2c(dut)
    device.mem[0x20:0x26] = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x20]) == 0
    assert await controller.read_blocking(DEVICE_ADDRESS, 6) == [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    assert device.events[-3:] == ["S", ("addr", DEVICE_ADDRESS, 1, True), "P"]
    assert_bus_idle(device)


@cocotb.test()
async def test_register_read_with_repeated_start(dut):
    """The usual register read: write the register number, repeated Start, then read."""
    controller, device = await start_i2c(dut)
    device.mem[0x40:0x43] = [0xCA, 0xFE, 0x42]
    received = []

    async def put(word):
        # Every byte on the bus is pushed to RX, so keep draining or the SM stalls
        await controller.put16(word)
        received.extend(w & 0xFF for w in await controller.drain_rx())

    await controller.start()
    await put((DEVICE_ADDRESS << 2) | 1)
    await put((0x40 << 1) | 1)
    await controller.repstart()
    await put((DEVICE_ADDRESS << 2) | 3)
    for index in range(3):
        last = (1 << FINAL_LSB) | 1 if index == 2 else 0
        await put((0xFF << 1) | last)
    await controller.stop()
    await controller.wait_idle()
    received.extend(w & 0xFF for w in await controller.drain_rx())
    # RX holds: address W, register, address R, then the three data bytes
    assert received[-3:] == [0xCA, 0xFE, 0x42], f"RX: {[hex(b) for b in received]}"
    assert device.events == [
        "S", ("addr", DEVICE_ADDRESS, 0, True), "Sr", ("addr", DEVICE_ADDRESS, 1, True), "P"
    ]
    assert not await controller.check_error()
    assert_bus_idle(device)


@cocotb.test()
async def test_address_nak_raises_flag_and_recovers(dut):
    controller, device = await start_i2c(dut)
    assert await controller.write_blocking(0x51, [0x01, 0x02, 0x03]) == -1, "a missing device wasn't reported"
    assert not await controller.check_error(), "the IRQ flag wasn't cleared by recovery"
    assert ("addr", 0x51, 0, False) in device.events
    assert device.events[-1] == "P", "recovery didn't end with a Stop"
    assert device.writes == []
    assert_bus_idle(device)

    # The controller must still work afterwards
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x05, 0x99]) == 0
    assert device.writes == [[0x05, 0x99]]


@cocotb.test()
async def test_data_nak_raises_flag_and_recovers(dut):
    controller, device = await start_i2c(dut, nak_data_after=2)
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x00, 0x01, 0x02, 0x03]) == -1
    assert not await controller.check_error()
    assert device.writes == [[0x00, 0x01]]
    assert device.events[-1] == "P"
    assert_bus_idle(device)


@cocotb.test()
async def test_nak_on_final_byte_is_ignored(dut):
    """The Final bit means a NAK on the last byte is expected, so no error."""
    controller, device = await start_i2c(dut, nak_data_after=2)
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x00, 0x01, 0x02]) == 0
    assert device.writes == [[0x00, 0x01]]


@cocotb.test()
async def test_bus_scan(dut):
    controller, device = await start_i2c(dut)
    found = []
    for address in range(0x4D, 0x54):
        if await controller.read_blocking(address, 0) == []:
            found.append(address)
    assert found == [DEVICE_ADDRESS]
    assert_bus_idle(device)


@cocotb.test()
async def test_clock_stretching(dut):
    controller, device = await start_i2c(dut, stretch=300)
    device.mem[0x30:0x32] = [0x5A, 0xA5]
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x30, 0x77]) == 0
    assert device.writes == [[0x30, 0x77]]
    assert device.mem[0x30] == 0x77
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x31]) == 0
    assert await controller.read_blocking(DEVICE_ADDRESS, 1) == [0xA5]
    gaps = [b - a for a, b in zip(device.scl_rises, device.scl_rises[1:])]
    assert max(gaps) >= 300, "the controller didn't wait for the stretched clock"


@cocotb.test()
async def test_scl_period_at_100khz_divider(dut):
    """Data bits take 32 program cycles, so clkdiv 16 gives ~97.7 kHz from 50 MHz.

    The program's own delays make the periods around each ACK a little different
    (31 and 34 cycles), so the check is on the data-bit period.
    """
    controller, device = await start_i2c(dut, clkdiv=16)
    assert await controller.write_blocking(DEVICE_ADDRESS, [0x01, 0x02]) == 0
    gaps = [b - a for a, b in zip(device.scl_rises, device.scl_rises[1:])]
    data_bit_period = max(set(gaps), key=gaps.count)
    assert data_bit_period == CYCLES_PER_BIT * 16, f"SCL periods seen: {sorted(set(gaps))}"
    assert min(gaps) >= 31 * 16, f"an SCL period was too short: {sorted(set(gaps))}"


@cocotb.test()
async def test_random_writes_and_reads(dut):
    controller, device = await start_i2c(dut)
    for _ in range(6):
        register = random.randrange(0, 200)
        data = [random.randrange(256) for _ in range(random.randrange(1, 5))]
        assert await controller.write_blocking(DEVICE_ADDRESS, [register] + data) == 0
        assert await controller.write_blocking(DEVICE_ADDRESS, [register]) == 0
        assert await controller.read_blocking(DEVICE_ADDRESS, len(data)) == data
    assert_bus_idle(device)
