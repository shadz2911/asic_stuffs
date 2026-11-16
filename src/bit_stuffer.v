`default_nettype none

module bit_stuffer (
    input wire clk,
    input wire rst_n,
    input wire new_packet,

    input wire [2:0] STUFF_COUNT,
    input wire BOTH,

    input wire data,
    input wire w_pulse,

    output wire out,
    output wire stuff
);

localparam COUNTING=0, STUFF=1;
reg state, next_state;

reg [2:0] counter, next_counter;
reg last_bit;

always @(*) begin
    case (state)
        COUNTING: begin
            next_counter = (last_bit == data) && (BOTH || data) ? counter + 3'd1 : 3'd0;
            next_state = next_counter == (STUFF_COUNT-1) ? STUFF : COUNTING;
        end
        STUFF: begin
            next_counter = 3'd0;
            next_state = COUNTING;
        end
    endcase
end

always @(posedge clk) begin
    if (!rst_n || new_packet) begin
        state <= COUNTING;
        counter <= 3'd0;
        last_bit <= 1'b0;
    end else if (w_pulse) begin
        state <= next_state;
        counter <= next_counter;
        last_bit <= (state == STUFF) ? !last_bit : data;
    end
end

assign out = state == STUFF ? !last_bit : data;
assign stuff = state == STUFF;

endmodule
