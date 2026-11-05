#!/usr/bin/env python3
"""Checks the test suite can fail: adds one known bug at a time and expects failures.

Each bug runs on a temporary copy of the repo, so src/ is never touched. Run it
with the venv active, from anywhere. Exits non-zero if any bug goes uncaught.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TIMEOUT_SECONDS = 300

# Skips the slow every-byte tests. No parentheses or pipes: this goes through a shell.
TEST_FILTER = "x[.]test_[^e]"

# name, file, original text, buggy text
BUGS = [
    ("control: no bug", "pio_sm.v", "wire is_jmp", "wire is_jmp"),
    ("JMP X-- off by one", "pio_sm.v", "3'b010: jmp_taken = x != 0;", "3'b010: jmp_taken = x > 1;"),
    ("delay ignores side-set bits", "pio_sm.v", "instr_data[12:8] & (5'b11111 >> sideset_count)", "instr_data[12:8]"),
    ("side-set skipped during stall", "pio_sm.v", "if ((sideset_count != 0) && (!side_en", "if (!stall && (sideset_count != 0) && (!side_en"),
    ("OUT shift direction flipped", "pio_sm.v", "out_shift_right = shiftctrl[19];", "out_shift_right = !shiftctrl[19];"),
    ("IN shift direction flipped", "pio_sm.v", "in_shift_right  = shiftctrl[18];", "in_shift_right  = !shiftctrl[18];"),
    ("WAIT polarity flipped", "pio_sm.v", "pins_in[wait_sum[2:0]])   != instr_data[7]", "pins_in[wait_sum[2:0]])   == instr_data[7]"),
    ("autopush uses the old count", "pio_sm.v", "wire to_auto = (isr_shift_count_data >=", "wire to_auto = (isr_shift_count >="),
    ("no stall on a full RX FIFO", "pio_sm.v", "|| (to_auto && (instr_data[15:13] == 3'b010) && rx_full)", ""),
    ("SPI req stuck high", "host_spi.v", "        req_reg <= 1'b0;\n        // the cycle", "        // the cycle"),
    ("abort doesn't reset bit count", "host_spi.v", "            bit_count <= 6'd0;\n        end else if (sck_rising)", "        end else if (sck_rising)"),
    ("single read pops twice", "host_spi.v", "bit_count <= bit_count + 6'd1;\n                    miso_shift <= rdata;", "bit_count <= bit_count + 6'd1;\n                    req_reg <= 1'b1;\n                    miso_shift <= rdata;"),
    ("burst never loads the next word", "host_spi.v", "if (req_d && !wr) miso_shift <= rdata;", ""),
    ("MISO driven between frames", "host_spi.v", "assign miso = miso_shift[31] && !cs_n_sync;", "assign miso = miso_shift[31];"),
    ("instruction memory writes dead", "host_regs.v", "assign mem_we = write_req &&", "assign mem_we = 1'b0 && write_req &&"),
    ("RX pop dead", "host_regs.v", "assign sm1_rx_pop = read_req &&", "assign sm1_rx_pop = 1'b0 && read_req &&"),
    ("empty RXF read returns junk", "host_regs.v", "rdata = sm1_rx_empty ? 32'd0 : sm1_rx_dout;", "rdata = sm1_rx_dout;"),
    ("SM0 enable ignored", "project.v", ".enable    (sm_enable[0])", ".enable    (1'b1)"),
    ("TX line resets low", "host_regs.v", "sm0_pins_init <= 8'hFF;", "sm0_pins_init <= 8'h00;"),
    ("pin start value not applied", "pio_sm.v", "delay_cnt   <= 5'd0;\n      pin_out_reg <= pins_init;\n    end else if (clkdiv_tick)", "delay_cnt   <= 5'd0;\n    end else if (clkdiv_tick)"),
    ("FIFO accepts pushes when full", "fifo.v", "if (push && (!full || pop)) begin", "if (push) begin"),
]


def run_bug(bug):
    name, filename, original, buggy = bug
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ignore = shutil.ignore_patterns("sim_build", "__pycache__", "*.fst", "results.xml")
        for folder in ("src", "test", "programs", "scripts"):
            shutil.copytree(REPO / folder, root / folder, ignore=ignore)

        source = root / "src" / filename
        text = source.read_text()
        if original not in text:
            return name, "NO MATCH", []
        source.write_text(text.replace(original, buggy, 1))

        test_dir = root / "test"
        env = dict(os.environ, PWD=str(test_dir), PYTHONPATH="")
        command = ["make", "-B", f"COCOTB_TEST_FILTER={TEST_FILTER}"]
        try:
            result = subprocess.run(command, cwd=test_dir, env=env, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return name, "TIMEOUT", []

        failed = [line.split()[1] for line in result.stdout.splitlines() if "** test" in line and " FAIL " in line]
        summary = [line for line in result.stdout.splitlines() if "TESTS=" in line]
        return name, ("ran" if summary else "NO RESULT"), failed


def main():
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_bug, BUGS))

    problems = 0
    for name, status, failed in results:
        is_control = name.startswith("control")
        if status != "ran":
            problems += 1
            print(f"PROBLEM  {name}: {status}")
        elif is_control and failed:
            problems += 1
            print(f"PROBLEM  {name}: {len(failed)} tests failed with no bug added")
        elif is_control:
            print(f"ok       {name}: all tests pass")
        elif failed:
            print(f"caught   {name}: {len(failed)} tests failed")
        else:
            problems += 1
            print(f"MISSED   {name}: every test still passed")
    print(f"\n{problems} problem(s) out of {len(results)} runs")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
