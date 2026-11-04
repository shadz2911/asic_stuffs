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

    // SM0 TX FIFO
    output wire sm0_tx_push,
    output wire [31:0] sm0_tx_din,

    // State machine enables
    output reg [1:0] sm_enable
);

// Instruction memory and write control
// assuming all requests to write instruction have 0x00XXXXX
// assuming all request to push data into TX FIFO have 'addr' == 0x30

wire write_req;
assign write_req = req && we;

assign mem_we = write_req && (addr[6:5] == 2'b00);
assign mem_waddr = addr[4:0];
assign mem_wdata = wdata[15:0];

assign sm0_tx_push = write_req && (addr == 7'h30);
assign sm0_tx_din = wdata;

// rdata assignment

always @(*) begin
    case (addr)
        7'h20: rdata = {30'd0, sm_enable};
        default: rdata = 32'd0;
    endcase
end

// SM enable control, assume 0x20 means we are writing to enable state machines

always @(posedge clk) begin
    if (!rst_n) begin
        sm_enable <= 2'b00;
    end else begin
        sm_enable <= write_req && (addr == 7'h20) ? wdata[1:0] : sm_enable;
    end
end

endmodule