`default_nettype none

module host_spi (
    input wire sck,
    input wire cs_n,
    input wire mosi,

    input wire clk,
    input wire rst_n,

    output wire req,
    output reg wr,
    output reg [6:0] addr,
    output reg [31:0] data
);

// synchronize serial clock, chip select and MOSI into ASIC clock domain

wire sck_sync, cs_n_sync, mosi_sync;

sync2 sck_job (.clk(clk), .rst_n(rst_n), .d(sck), .q(sck_sync));
sync2 cs_n_job (.clk(clk), .rst_n(rst_n), .d(cs_n), .q(cs_n_sync));
sync2 mosi_job (.clk(clk), .rst_n(rst_n), .d(mosi), .q(mosi_sync));

reg sck_prev;

always @(posedge clk) begin
    sck_prev <= sck_sync;
end

wire sck_rising;
assign sck_rising = (sck_prev == 1'b0) && (sck_sync == 1'b1);

// frame read state machine

reg writing;
reg [5:0] bit_count;
reg req_reg;

always @(posedge clk) begin
    if (!rst_n) begin
        bit_count <= 6'd0;
        req_reg <= 1'b0;
    end else begin
        // one-cycle pulse: low unless the last data bit arrives this cycle
        req_reg <= 1'b0;
        if (cs_n_sync) begin
            bit_count <= 6'd0;
        end else if (sck_rising) begin
            if (bit_count == 6'd0) begin
                bit_count <= 6'd1;
                wr <= mosi_sync;
            end else if (bit_count < 6'd8) begin
                bit_count <= bit_count + 6'd1;
                addr <= {addr[5:0], mosi_sync};
            end else if (bit_count < 6'd39) begin
                bit_count <= bit_count + 6'd1;
                data <= {data[30:0], mosi_sync};
            end else begin
                // last data bit: back to 8 so a burst continues with the next word
                bit_count <= 6'd8;
                data <= {data[30:0], mosi_sync};
                req_reg <= 1'b1;
            end
        end
    end
end

assign req = req_reg;

endmodule