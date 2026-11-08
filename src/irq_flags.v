`default_nettype none

module irq_flags #(
    parameter NUM_SMS = 2
) (
    input wire clk,
    input wire rst_n,

    // One entry per SM, SM0 in the lowest bits
    input wire [3*NUM_SMS-1:0] addr,
    input wire [NUM_SMS-1:0] we,
    input wire [NUM_SMS-1:0] wdata,
    input wire [7:0] host_clear,

    output wire [NUM_SMS-1:0] rdata,
    output wire [7:0] flags_out
);

reg [7:0] flags;
reg [7:0] set_mask, clear_mask;
integer i;

always @(*) begin
    set_mask = 8'd0;
    clear_mask = 8'd0;
    for (i = 0; i < NUM_SMS; i = i + 1) begin
        if (we[i]) begin
            if (wdata[i]) set_mask[addr[3*i +: 3]] = 1'b1;
            else clear_mask[addr[3*i +: 3]] = 1'b1;
        end
    end
end

always @(posedge clk) begin
    if (!rst_n) begin
        flags <= 8'd0;
    end else begin
        flags <= (flags & ~(clear_mask | host_clear)) | set_mask;  // a set beats a clear
    end
end

assign flags_out = flags;

genvar g;
generate
    for (g = 0; g < NUM_SMS; g = g + 1) begin : g_rdata
        assign rdata[g] = flags[addr[3*g +: 3]];
    end
endgenerate

endmodule