opcode 001
instr_data[7] tells you which polarity to wait for
instr_data[4:0] tells you which pin to look
instr_data[6:5] decides whether to compare gpio directly, in_base + [4:0] or an IRQ flag (not done yet)
