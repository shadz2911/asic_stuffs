#!/usr/bin/env python3
"""Assemble PIO programs and pack them into one instruction-memory image.

Usage: assemble.py <output.hex> <prog1.pio> [<prog2.pio> ...]

Programs are placed back to back in the order given (first at address 0).
Like the Pico SDK's pio_add_program(), JMP targets are relocated by each
program's offset, because pioasm assembles every program as if it starts at 0.

The output is 32 lines (one 16-bit word each, 4 hex digits, unused words 0000)
for $readmemh in instr_mem.v. For each program the script prints the absolute
START_PC / WRAP_BOTTOM / WRAP_TOP / SIDESET_COUNT values to use as pio_sm
parameters.

Assembler: the official `pioasm` if on PATH, else the adafruit-circuitpython-pioasm
package. Which one was used is printed.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MEM_WORDS = 32  # instruction memory depth (see src/instr_mem.v)


def assemble_with_pioasm(src: Path):
    """Return (words, wrap_target, wrap, sideset_count) using the official pioasm.

    The c-sdk output is parsed because, unlike `-o hex`, it also carries the
    wrap points and side-set configuration.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "prog.h"
        subprocess.run(["pioasm", "-o", "c-sdk", str(src), str(out)], check=True)
        text = out.read_text()
    words = [int(w, 16) for w in re.findall(r"^\s*0x([0-9a-fA-F]{4}),", text, flags=re.M)]
    wrap_target = int(re.search(r"#define \w+_wrap_target (\d+)", text).group(1))
    wrap = int(re.search(r"#define \w+_wrap (\d+)", text).group(1))
    # sm_config_set_sideset(&c, <bit_count incl. opt>, <optional>, <pindirs>) - absent if no side-set
    m = re.search(r"sm_config_set_sideset\(&c, (\d+),", text)
    sideset_count = int(m.group(1)) if m else 0
    return words, wrap_target, wrap, sideset_count


def assemble_with_adafruit(src: Path):
    """Same as assemble_with_pioasm(), using the pure-Python assembler."""
    import adafruit_pioasm  # imported lazily: only needed on this path

    text = src.read_text()
    # Strip what only the official pioasm understands (C helper blocks,
    # .pio_version, // comments) rather than editing the attributed sources.
    text = re.sub(r"^%\s*c-sdk\s*\{.*?^%\}", "", text, flags=re.S | re.M)
    text = re.sub(r"^\s*\.pio_version\b.*$", "", text, flags=re.M)
    text = re.sub(r"//.*$", "", text, flags=re.M)

    program = adafruit_pioasm.Program(text)
    words = list(program.assembled)
    kw = program.pio_kwargs
    # Without .wrap directives the program wraps from its last instruction to its first
    wrap_target = kw.get("wrap_target", 0)
    wrap = kw.get("wrap", len(words) - 1)
    # Hardware SIDESET_COUNT includes the enable bit when side-set is `opt`
    sideset_count = kw.get("sideset_pin_count", 0) + (1 if kw.get("sideset_enable") else 0)
    return words, wrap_target, wrap, sideset_count


def relocate(words, offset):
    """Add `offset` to the 5-bit target of every JMP (opcode bits [15:13] == 000)."""
    out = []
    for w in words:
        if (w >> 13) == 0b000:
            w = (w & ~0x1F) | (((w & 0x1F) + offset) & 0x1F)
        out.append(w)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    dst = Path(sys.argv[1])
    sources = [Path(p) for p in sys.argv[2:]]

    if shutil.which("pioasm"):
        name, assemble = "official pioasm", assemble_with_pioasm
    else:
        name, assemble = "adafruit-circuitpython-pioasm (pioasm not on PATH)", assemble_with_adafruit
    print(f"assemble.py: using {name}")

    image = []
    for src in sources:
        words, wrap_target, wrap, sideset_count = assemble(src)
        offset = len(image)
        if offset + len(words) > MEM_WORDS:
            raise SystemExit(f"assemble.py: {src.name} does not fit ({offset + len(words)} > {MEM_WORDS} words)")
        image += relocate(words, offset)
        print(f"  {src.name}: {len(words)} words at {offset} -> "
              f"START_PC={offset} WRAP_BOTTOM={offset + wrap_target} "
              f"WRAP_TOP={offset + wrap} SIDESET_COUNT={sideset_count}")

    image += [0] * (MEM_WORDS - len(image))  # pad so $readmemh sees a full image
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("".join(f"{w:04x}\n" for w in image))
    print(f"  wrote {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
