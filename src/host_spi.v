`default_nettype none

module host_spi (
    input wire sck,
    input wire cs_n,
    input wire mosi,
    input wire [31:0] rdata,

    input wire clk,
    input wire rst_n,

    output wire req,
    output reg wr,
    output reg [6:0] addr,
    output reg [31:0] data,
    output wire miso
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

reg [5:0] bit_count;
reg [31:0] miso_shift;
reg req_reg;
reg req_d;

always @(posedge clk) begin
    if (!rst_n) begin
        bit_count <= 6'd0;
        req_reg <= 1'b0;
        req_d <= 1'b0;
        miso_shift <= 32'd0; // can be optimized out later
    end else begin
        // one-cycle pulse: low unless the last data bit arrives this cycle
        req_reg <= 1'b0;
        // the cycle after a read's req, rdata shows the next word for a burst
        req_d <= req_reg;
        if (req_d && !wr) miso_shift <= rdata;
        if (cs_n_sync) begin
            bit_count <= 6'd0;
        end else if (sck_rising) begin
            if (bit_count == 6'd0) begin
                bit_count <= 6'd1;
                wr <= mosi_sync;
            end else if (bit_count < 6'd8) begin
                bit_count <= bit_count + 6'd1;
                addr <= {addr[5:0], mosi_sync};
            end else if (wr) begin
                if (bit_count < 6'd39) begin
                    bit_count <= bit_count + 6'd1;
                    data <= {data[30:0], mosi_sync};
                end else begin
                    // last data bit: back to 8 so a burst continues with the next word
                    bit_count <= 6'd8;
                    data <= {data[30:0], mosi_sync};
                    req_reg <= 1'b1;
                end
            end else begin
                if (bit_count < 6'd15) begin
                    bit_count <= bit_count + 6'd1;
                end else if (bit_count == 6'd15) begin
                    bit_count <= bit_count + 6'd1;
                    miso_shift <= rdata;
                end else if (bit_count < 6'd47) begin
                    bit_count <= bit_count + 6'd1;
                    miso_shift <= miso_shift << 1;
                end else begin
                    bit_count <= 6'd16;
                    req_reg <= 1'b1;
                end
            end
        end
    end
end

assign miso = miso_shift[31] && !cs_n_sync;

// For a read request, the req flag only triggers side effects (i.e. popping the rx fifo)
// it can already see rdata

assign req = req_reg;

endmodule