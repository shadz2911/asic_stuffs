Has opcode 100
instr_data[6] decides whether to pull or not if the osr (output shift register) is full (if 1, pull will skip)
instr_data[7] decides whether the pull is blocking (stall until fifo is no longer empty) or not (skip)
The output_shift_count starts at 32 for a completely empty register, then 0 for a new pull, then slowly ticks up to 32 as bits are shifted out