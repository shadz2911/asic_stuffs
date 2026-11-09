`default_nettype none

module host_regs (
    input wire clk,
    input wire rst_n,

    // Request bus driven by host_spi
    input wire req,
    input wire we,
    input wire [6:0] addr,
    input wire [31:0] wdata,
    output reg [31:0] rdata,

    // Imem write port
    output wire mem_we,
    output wire [4:0] mem_waddr,
    output wire [15:0] mem_wdata,

    // Interrrupt flags
    input wire [7:0] rdata_irq,
    output wire [7:0] clear_irq,

    // SM0 TX/RX FIFOs
    input wire [31:0] sm0_rx_dout,
    input wire sm0_rx_empty,
    input wire sm0_rx_full,
    input wire sm0_tx_empty,
    input wire sm0_tx_full,
    output wire sm0_tx_push,
    output wire [31:0] sm0_tx_din,
    output wire sm0_rx_pop,
    output reg [7:0] sm0_pindirs_init,

    // SM1 TX/RX FIFO
    input wire [31:0] sm1_rx_dout,
    input wire sm1_rx_empty,
    input wire sm1_rx_full,
    input wire sm1_tx_empty,
    input wire sm1_tx_full,
    output wire sm1_tx_push,
    output wire [31:0] sm1_tx_din,
    output wire sm1_rx_pop,
    output reg [7:0] sm1_pindirs_init,

    // State machine enables
    output reg [1:0] sm_enable,

    // SM0 config
    output reg [31:0] sm0_clkdiv,
    output reg [31:0] sm0_execctrl,
    output reg [31:0] sm0_shiftctrl,
    output reg [31:0] sm0_pinctrl,
    output reg [4:0] sm0_start_pc,
    output reg [7:0] sm0_pins_init,

    // SM1 config
    output reg [31:0] sm1_clkdiv,
    output reg [31:0] sm1_execctrl,
    output reg [31:0] sm1_shiftctrl,
    output reg [31:0] sm1_pinctrl,
    output reg [4:0] sm1_start_pc,
    output reg [7:0] sm1_pins_init
);

// Instruction memory and write control
// assuming all requests to write instruction have 0x00XXXXX
// assuming all request to push data into TX FIFO have 'addr' == 0x30

wire write_req, read_req;
assign write_req = req && we;
assign read_req = req && !we;

assign mem_we = write_req && (addr[6:5] == 2'b00);
assign mem_waddr = addr[4:0];
assign mem_wdata = wdata[15:0];

assign sm0_tx_push = write_req && (addr == 7'h30);
assign sm0_tx_din = wdata;

assign sm1_tx_push = write_req && (addr == 7'h40);
assign sm1_tx_din = wdata;

assign sm0_rx_pop = read_req && (addr == 7'h31);

assign sm1_rx_pop = read_req && (addr == 7'h41);

// rdata assignment

always @(*) begin
    case (addr)
        7'h20: rdata = {30'd0, sm_enable};
        7'h21: rdata = {6'd0, sm1_tx_empty, sm0_tx_empty,
                6'd0, sm1_tx_full,  sm0_tx_full,
                6'd0, sm1_rx_empty, sm0_rx_empty,
                6'd0, sm1_rx_full,  sm0_rx_full};
        7'h22: rdata = {24'd0, rdata_irq};
        7'h31: rdata = sm0_rx_empty ? 32'd0 : sm0_rx_dout;
        7'h32: rdata = sm0_clkdiv;
        7'h33: rdata = sm0_execctrl;
        7'h34: rdata = sm0_shiftctrl;
        7'h35: rdata = sm0_pinctrl;
        7'h36: rdata = {27'd0, sm0_start_pc};
        7'h38: rdata = {24'd0, sm0_pins_init};
        7'h39: rdata = {24'd0, sm0_pindirs_init};
        7'h41: rdata = sm1_rx_empty ? 32'd0 : sm1_rx_dout;
        7'h42: rdata = sm1_clkdiv;
        7'h43: rdata = sm1_execctrl;
        7'h44: rdata = sm1_shiftctrl;
        7'h45: rdata = sm1_pinctrl;
        7'h46: rdata = {27'd0, sm1_start_pc};
        7'h48: rdata = {24'd0, sm1_pins_init};
        7'h49: rdata = {24'd0, sm1_pindirs_init};
        default: rdata = 32'd0;
    endcase
end

// Interrupt clear control

assign clear_irq = (addr == 7'h22) && write_req ? wdata[7:0] : 8'd0;

// SM enable control, assume 0x20 means we are writing to enable state machines

always @(posedge clk) begin
    if (!rst_n) begin
        sm_enable <= 2'b00;
    end else begin
        sm_enable <= write_req && (addr == 7'h20) ? wdata[1:0] : sm_enable;
    end
end

// SM config, reset to the RP2040 defaults

always @(posedge clk) begin
    if (!rst_n) begin
        sm0_clkdiv <= 32'h0001_0000;
        sm0_execctrl <= 32'h0001_F000;
        sm0_shiftctrl <= 32'h000C_0000;
        sm0_pinctrl <= 32'h1400_0000;
        sm0_start_pc <= 5'd0;
        sm0_pins_init <= 8'hFF; // lines idle high until the host says otherwise
        sm0_pindirs_init <= 8'd0;
        sm1_clkdiv <= 32'h0001_0000;
        sm1_execctrl <= 32'h0001_F000;
        sm1_shiftctrl <= 32'h000C_0000;
        sm1_pinctrl <= 32'h1400_0000;
        sm1_start_pc <= 5'd0;
        sm1_pins_init <= 8'hFF;
        sm1_pindirs_init <= 8'd0;
    end else begin
        sm0_clkdiv <= write_req && (addr == 7'h32) ? wdata : sm0_clkdiv;
        sm0_execctrl <= write_req && (addr == 7'h33) ? wdata : sm0_execctrl;
        sm0_shiftctrl <= write_req && (addr == 7'h34) ? wdata : sm0_shiftctrl;
        sm0_pinctrl <= write_req && (addr == 7'h35) ? wdata : sm0_pinctrl;
        sm0_start_pc <= write_req && (addr == 7'h36) ? wdata[4:0] : sm0_start_pc;
        sm0_pins_init <= write_req && (addr == 7'h38) ? wdata[7:0] : sm0_pins_init;
        sm0_pindirs_init <= write_req && (addr == 7'h39) ? wdata[7:0] : sm0_pindirs_init;
        sm1_clkdiv <= write_req && (addr == 7'h42) ? wdata : sm1_clkdiv;
        sm1_execctrl <= write_req && (addr == 7'h43) ? wdata : sm1_execctrl;
        sm1_shiftctrl <= write_req && (addr == 7'h44) ? wdata : sm1_shiftctrl;
        sm1_pinctrl <= write_req && (addr == 7'h45) ? wdata : sm1_pinctrl;
        sm1_start_pc <= write_req && (addr == 7'h46) ? wdata[4:0] : sm1_start_pc;
        sm1_pins_init <= write_req && (addr == 7'h48) ? wdata[7:0] : sm1_pins_init;
        sm1_pindirs_init <= write_req && (addr == 7'h49) ? wdata[7:0] : sm1_pindirs_init;
    end
end

endmodule
