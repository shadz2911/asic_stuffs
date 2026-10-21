Controlled by a jmp_taken and jmp_target
The jmp_target is always instr_data[4:0] 
jmp_taken is decided by instr_data[7:5], with 000 being always, 001 if x is 0, and other conditions
Since the jmp needs to happen in the same cycle it is issued without delay, jmp_target and jmp_taken are combinational then take effect in the following clock cycle
JMP's opcode is 000