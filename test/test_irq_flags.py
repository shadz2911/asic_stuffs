# SPDX-License-Identifier: Apache-2.0
"""IRQ flags: the IRQ and WAIT IRQ instructions and the host flag register.

Each test loads its own small program over SPI and judges the result at the pins.
SM0 reports progress on uo_out[0]. SM1's pins aren't bonded out, so it reports by
pushing to its RX FIFO or by setting a flag that SM0 is waiting on.
"""

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge

from test_uart_tx import ADDR_CTRL, ADDR_INSTR, reset_chip

ADDR_FSTAT = 0x21
ADDR_IRQ = 0x22
SM0_REGS = 0x30
SM1_REGS = 0x40
REG_CLKDIV = 2
REG_EXECCTRL = 3
REG_PINCTRL = 5
REG_START_PC = 6
REG_PINS_INIT = 8

SM0 = 0x1
SM1 = 0x2
BOTH = SM0 | SM1

SM1_PROGRAM_BASE = 16
HALF_PERIOD = 4  # SCK = clk/8
SETTLE = 300  # clk cycles that are plenty for a clkdiv=1 program to react
FSTAT_SM1_RX_EMPTY = 1 << 9

EXECCTRL_NO_WRAP = 0x0001F000  # wrap 31 -> 0, so programs just run forward
EXECCTRL_WRAP_0 = 0x00000000  # wrap 0 -> 0: repeat the instruction at address 0
PINCTRL_SET_PIN_0 = 1 << 26  # SET drives one pin, starting at pin 0

REL = 0x10  # index bit 4: add the SM number


def irq_set(index, delay=0):
    return 0xC000 | (delay << 8) | index


def irq_wait(index, delay=0):
    return 0xC020 | (delay << 8) | index


def irq_clear(index, wait_bit=False):
    return 0xC040 | (int(wait_bit) << 5) | index


def wait_irq(polarity, index):
    return 0x2040 | (polarity << 7) | index


def set_pin(level):
    return 0xE000 | level


def idle(delay):
    return 0xE040 | (delay << 8)  # SET Y, 0: an instruction with no visible effect


PUSH_BLOCK = 0x8020


async def start(dut, sm0, sm1=(), clkdiv0=1, clkdiv1=1, sm0_execctrl=EXECCTRL_NO_WRAP, start_clock=True):
    """Resets, loads both programs (each ends in a jump to itself) and configures the SMs."""
    host = await reset_chip(dut, HALF_PERIOD, start_clock=start_clock)
    for base, program in ((0, sm0), (SM1_PROGRAM_BASE, sm1)):
        for offset, word in enumerate(list(program) + [base + len(program)]):
            await host.write(ADDR_INSTR + base + offset, word)

    for regs, clkdiv, execctrl, start_pc in (
        (SM0_REGS, clkdiv0, sm0_execctrl, 0),
        (SM1_REGS, clkdiv1, EXECCTRL_NO_WRAP, SM1_PROGRAM_BASE),
    ):
        await host.write(regs + REG_CLKDIV, clkdiv << 16)
        await host.write(regs + REG_EXECCTRL, execctrl)
        await host.write(regs + REG_PINCTRL, PINCTRL_SET_PIN_0)
        await host.write(regs + REG_START_PC, start_pc)
        await host.write(regs + REG_PINS_INIT, 0)
    await ClockCycles(dut.clk, 5)
    return host


def pin(dut):
    return dut.uo_out.value.to_unsigned() & 1


async def pin_becomes(dut, level, cycles=SETTLE):
    for _ in range(cycles):
        await FallingEdge(dut.clk)
        if pin(dut) == level:
            return
    assert False, f"pin never reached {level}"


async def pin_stays(dut, level, cycles=SETTLE):
    for _ in range(cycles):
        await FallingEdge(dut.clk)
        assert pin(dut) == level, f"pin left {level}"


async def sm1_has_pushed(host):
    return not await host.read(ADDR_FSTAT) & FSTAT_SM1_RX_EMPTY


@cocotb.test()
async def test_flag_set_by_one_sm_releases_the_other(dut):
    host = await start(dut, [wait_irq(1, 3), set_pin(1)], [irq_set(3)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_wait_1_consumes_the_flag(dut):
    host = await start(dut, [wait_irq(1, 3), set_pin(1), wait_irq(1, 3), set_pin(0)], [irq_set(3)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)
    await pin_stays(dut, 1)


@cocotb.test()
async def test_wait_0_stalls_until_flag_clears(dut):
    host = await start(dut, [irq_set(3), wait_irq(0, 3), set_pin(1)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_IRQ, 1 << 3)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_irq_clear_lowers_the_flag(dut):
    host = await start(dut, [irq_set(5), irq_clear(5), wait_irq(1, 5), set_pin(1)], [irq_set(5)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_CTRL, BOTH)  # the stall was the missing flag: a fresh set releases it
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_clear_ignores_the_wait_bit(dut):
    host = await start(dut, [irq_clear(1, wait_bit=True), set_pin(1)])
    await host.write(ADDR_CTRL, SM0)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_irq_wait_holds_until_host_clears(dut):
    # The idle keeps the pin high long enough to outlast the end of the host's SPI write
    host = await start(dut, [irq_wait(2), set_pin(1), idle(31), wait_irq(0, 2), set_pin(0)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_IRQ, 1 << 2)
    await pin_becomes(dut, 1)
    # The flag must have stayed low after release, so the WAIT 0 goes straight through
    await pin_becomes(dut, 0)


@cocotb.test()
async def test_irq_wait_released_by_other_sm(dut):
    host = await start(dut, [irq_wait(4), set_pin(1)], [wait_irq(1, 4)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_irq_wait_on_a_flag_already_set(dut):
    host = await start(dut, [irq_set(6), irq_wait(6), set_pin(1)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_IRQ, 1 << 6)
    await pin_becomes(dut, 1)


async def cycles_until_release(dut, delay, start_clock):
    """clk cycles from the start of the host's clear to SM0's next instruction."""

    async def count():
        cycles = 0
        while pin(dut) == 0:
            await FallingEdge(dut.clk)
            cycles += 1
            assert cycles < 5000, "SM never released"
        return cycles

    host = await start(dut, [irq_wait(2, delay=delay), set_pin(1)], start_clock=start_clock)
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0, 50)
    counter = cocotb.start_soon(count())
    await host.write(ADDR_IRQ, 1 << 2)
    return await counter


@cocotb.test()
async def test_irq_wait_delay_starts_after_release(dut):
    without_delay = await cycles_until_release(dut, 0, start_clock=True)
    with_delay = await cycles_until_release(dut, 6, start_clock=False)
    assert with_delay - without_delay == 6, f"{without_delay} vs {with_delay} cycles"


@cocotb.test()
async def test_set_beats_clear_from_other_sm(dut):
    # Both SMs start on the same clock, so the set and the clear land together
    host = await start(dut, [irq_set(3), wait_irq(1, 3), set_pin(1)], [irq_clear(3)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_set_beats_host_clear(dut):
    # SM0 repeats IRQ SET every clock, so the host's clear has to collide with a set.
    # SM1 is watching every clock: if the flag ever drops, it pushes.
    host = await start(dut, [irq_set(3)], [wait_irq(0, 3), PUSH_BLOCK], sm0_execctrl=EXECCTRL_WRAP_0)
    await host.write(ADDR_CTRL, SM0)
    await ClockCycles(dut.clk, 20)
    await host.write(ADDR_CTRL, BOTH)
    await host.write(ADDR_IRQ, 1 << 3)
    await ClockCycles(dut.clk, 20)
    assert not await sm1_has_pushed(host), "flag dropped although a set collided with the clear"

    # With SM0 stopped there is no set to collide with, so the same clear gets through
    await host.write(ADDR_CTRL, SM1)
    await host.write(ADDR_IRQ, 1 << 3)
    await ClockCycles(dut.clk, 20)
    assert await sm1_has_pushed(host), "host clear never reached the flag"


@cocotb.test()
async def test_both_sms_set_different_flags_together(dut):
    host = await start(dut, [irq_set(1), wait_irq(1, 1), wait_irq(1, 6), set_pin(1)], [irq_set(6)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_host_clear_only_touches_bits_written_as_1(dut):
    host = await start(dut, [irq_set(1), irq_set(2), wait_irq(0, 1), set_pin(1), wait_irq(0, 2), set_pin(0)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_IRQ, 0xFFFFFF00)  # zero in every flag bit, ones above
    await pin_stays(dut, 0)
    await host.write(ADDR_IRQ, 1 << 1)
    await pin_becomes(dut, 1)
    await pin_stays(dut, 1)  # flag 2 is still up
    await host.write(ADDR_IRQ, 1 << 2)
    await pin_becomes(dut, 0)


@cocotb.test()
async def test_host_clear_lasts_one_write(dut):
    # The flag is set well after the host's write, while addr still points at the register
    host = await start(dut, [idle(31), irq_set(3), wait_irq(0, 3), set_pin(1)], clkdiv0=40)
    await host.write(ADDR_CTRL, SM0)
    await host.write(ADDR_IRQ, 1 << 3)
    await pin_stays(dut, 0, 4000)


async def check_set_fires_once(dut, sm0_first, clkdiv0, start_clock):
    # SM1 consumes the flag, reports through flag 7, then needs a second set it must not get
    host = await start(
        dut,
        [sm0_first, wait_irq(1, 7), set_pin(1)],
        [wait_irq(1, 3), irq_set(7), wait_irq(1, 3), PUSH_BLOCK],
        clkdiv0=clkdiv0,
        start_clock=start_clock,
    )
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1, SETTLE * 4)
    await ClockCycles(dut.clk, 20)
    assert not await sm1_has_pushed(host), "IRQ SET fired more than once"


@cocotb.test()
async def test_set_fires_once_during_its_delay(dut):
    await check_set_fires_once(dut, irq_set(3, delay=31), 1, start_clock=True)


@cocotb.test()
async def test_set_fires_once_between_clkdiv_ticks(dut):
    await check_set_fires_once(dut, irq_set(3), 200, start_clock=True)


@cocotb.test()
async def test_relative_index_uses_sm_number(dut):
    # (index with REL, flag SM1 should reach): wraps within 0-3, bit 2 unchanged
    for run, (index, flag) in enumerate([(1, 2), (3, 0), (5, 6), (7, 4)]):
        host = await start(dut, [wait_irq(1, flag), set_pin(1)], [irq_set(REL | index)], start_clock=run == 0)
        await host.write(ADDR_CTRL, BOTH)
        await pin_becomes(dut, 1)


@cocotb.test()
async def test_relative_index_doesnt_touch_the_absolute_flag(dut):
    host = await start(dut, [wait_irq(1, 1), set_pin(1)], [irq_set(REL | 1)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_stays(dut, 0)


@cocotb.test()
async def test_relative_index_on_sm0_adds_nothing(dut):
    host = await start(dut, [irq_set(REL | 3), wait_irq(1, 3), set_pin(1)])
    await host.write(ADDR_CTRL, SM0)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_wait_uses_relative_index(dut):
    # SM1's WAIT on flag 1 REL means flag 2, so a set of flag 2 releases it
    host = await start(dut, [irq_set(2), wait_irq(1, 7), set_pin(1)], [wait_irq(1, REL | 1), irq_set(7)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_wait_relative_ignores_the_absolute_flag(dut):
    host = await start(dut, [irq_set(1), wait_irq(1, 7), set_pin(1)], [wait_irq(1, REL | 1), irq_set(7)])
    await host.write(ADDR_CTRL, BOTH)
    await pin_stays(dut, 0)


@cocotb.test()
async def test_disable_mid_wait_restarts_the_handshake(dut):
    host = await start(dut, [irq_wait(2), set_pin(1)])
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)
    await host.write(ADDR_CTRL, 0)
    await host.write(ADDR_IRQ, 1 << 2)
    await host.write(ADDR_CTRL, SM0)
    await pin_stays(dut, 0)  # it set the flag again and went back to waiting
    await host.write(ADDR_IRQ, 1 << 2)
    await pin_becomes(dut, 1)


@cocotb.test()
async def test_host_reads_flags_without_changing_them(dut):
    host = await start(dut, [irq_set(1), irq_set(5)])
    assert await host.read(ADDR_IRQ) == 0, "flags not clear after reset"
    await host.write(ADDR_CTRL, SM0)
    await ClockCycles(dut.clk, 20)
    assert await host.read(ADDR_IRQ) == 0x22
    assert await host.read(ADDR_IRQ) == 0x22, "reading changed the flags"
    await host.write(ADDR_IRQ, 1 << 1)
    assert await host.read(ADDR_IRQ) == 0x20
