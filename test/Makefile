# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
# Every RTL file in src/ (info.yaml still lists them by hand for the TT flow)
PROJECT_SOURCES = $(notdir $(wildcard $(SRC_DIR)/*.v))

# PIO programs, packed into one instruction-memory image in this order
# (scripts/assemble.py prints the resulting START_PC / WRAP values)
PROG_DIR     = $(PWD)/../programs
PIO_PROGRAMS = $(PROG_DIR)/uart_tx.pio $(PROG_DIR)/uart_rx_mini.pio
PROGRAM_HEX  = $(PWD)/build/uart.hex
PYTHON      ?= python3

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))
# SIM enables the $readmemh preload in instr_mem.v; the image path goes in as a tb parameter
COMPILE_ARGS    += -DSIM
COMPILE_ARGS    += -Ptb.PROGRAM_HEX=\"$(PROGRAM_HEX)\"

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
COCOTB_TEST_MODULES = test

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# Assemble the programs before the simulation runs (results file = the sim run;
# defined after the include because COCOTB_RESULTS_FILE comes from cocotb)
$(COCOTB_RESULTS_FILE): $(PROGRAM_HEX)

$(PROGRAM_HEX): $(PIO_PROGRAMS) $(PWD)/../scripts/assemble.py
	$(PYTHON) $(PWD)/../scripts/assemble.py $@ $(PIO_PROGRAMS)

.PHONY: hex
hex: $(PROGRAM_HEX)
