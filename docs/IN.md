opcode 010
Functions the same as OUT but in reverse
instr_data[7:5] decides where to shift data in from (pins_in, x, y, isr, osr)
instr_data[4:0] decides how many bits to shift in
in_shift_right decides if bits are shifted in from the right (1) or left (0)