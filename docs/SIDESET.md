Remember that an instruction looks like the following:

 15  14  13 │ 12  11  10   9   8 │  7   6   5   4   3   2   1   0
────────────┼────────────────────┼────────────────────────────────
   OPCODE   │   5 "extra" bits   │   OPERANDS (what the instr does)
  (what     │  shared between    │   e.g. SET: destination + value
   instr)   │  side-set + delay  │        JMP: condition + address

- Bits 12 to 8 are shared between side-set and delay, with the number allocated to each being decided by sideset_count
- The allocation is as follows:

sideset_count │  12   11   10    9    8  │ delay can be
──────────────┼──────────────────────────┼─────────────
      0       │   D    D    D    D    D  │  0..31
      1       │   S    D    D    D    D  │  0..15
      2       │   S    S    D    D    D  │  0..7
      3       │   S    S    S    D    D  │  0..3
      4       │   S    S    S    S    D  │  0..1
      5       │   S    S    S    S    S  │  none

- The sidepindir decides whether to write pins as inputs or ouputs in pins_oe, or just write pins directly in pin_out
- sideset_base decides the 'base' pin or pindir to start writing
- You then set the pins or pindirs from sidesetbase and count up in sideset_count
- Bit `13 - sideset_count` goes to pin `sideset_base`, the next bit up goes to `sideset_base + 1`, and so on, for `sideset_count - side_en` pins.
- if side_en is 1, this allows the sideset to be enabled or disabled, and takes up one extra bit (the top bit, 12)

Extra notes:
- the sideset operates on an instruction issue, stall or not
