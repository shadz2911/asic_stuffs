`default_nettype none

module nrzi_encoder (
    input wire clk,
    input wire rst_n,
    input wire new_packet,

    input wire w_pulse,
    input wire data,

    output wire enc
);

reg line;

always @(posedge clk) begin
    if (!rst_n || new_packet) begin
        // give J a value of '1', will take car of D+ and D- lines in pin driver
        // this keeps things more flexible
        line <= 1'b1;
    end else begin
        line <= w_pulse && !data ? ~line : line;
    end
end

assign enc = line;

endmodule
