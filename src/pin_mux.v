`default_nettype none

module pin_mux #(
    parameter NUM_SMS = 2
) (
    // 8 bits per SM, SM0 in the lowest bits
    input wire [8*NUM_SMS-1:0] sm_pins_out,
    input wire [8*NUM_SMS-1:0] sm_pins_oe,

    output reg [7:0] pins_out,
    output reg [7:0] pins_oe
);

integer s, p;

always @(*) begin
    pins_out = 8'd0;
    pins_oe = 8'd0;
    for (s = 0; s < NUM_SMS; s = s + 1) begin
        for (p = 0; p < 8; p = p + 1) begin
            // A later SM overwrites an earlier one, so the highest-numbered SM wins
            if (sm_pins_oe[8*s + p]) begin
                pins_oe[p] = 1'b1;
                pins_out[p] = sm_pins_out[8*s + p];
            end
        end
    end
end

endmodule
