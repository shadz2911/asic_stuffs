`default_nettype none

module nrzi_decoder (
    input wire clk,
    input wire rst_n,
    input wire end_packet,

    input wire w_pulse,
    input wire data,

    output wire dec
);

reg data_prev;

always @(posedge clk) begin
    if (!rst_n || end_packet) begin
        data_prev <= 1'b1;
    end else begin
        data_prev <= w_pulse ? data : data_prev;
    end
end

assign dec = !(data ^ data_prev);

endmodule
