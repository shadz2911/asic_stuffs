## `clkdiv`: how fast the SM runs (datasheet 3.5.5)

|Bits|Field|Meaning|
|---|---|---|
|[31:16]|INT|integer divider: one tick every INT clk cycles (0 = 65536)|
|[15:8]|FRAC|fractional part; ignored in your design|

## `execctrl`: how the program executes (wrap, side-set mode, JMP pin)

|Bits|Field|Your wire|Meaning|
|---|---|---|---|
|30|SIDE_EN|`side_en`|side-set is `opt`: the top side-set bit is an enable|
|29|SIDE_PINDIR|`side_pindir`|side-set writes pin directions instead of values|
|[28:24]|JMP_PIN|`jmp_pin`|which pin `jmp pin` tests|
|[16:12]|WRAP_TOP|`wrap_top`|after this address…|
|[11:7]|WRAP_BOTTOM|`wrap_bottom`|…go back to this one|

Other bits (status select, out-enable tricks) aren't supported here.

## `shiftctrl`: how the shift registers behave (datasheet 3.5.4)

|Bits|Field|Your wire|Meaning|
|---|---|---|---|
|[29:25]|PULL_THRESH|`pull_thresh`|bits shifted out of the OSR before autopull refills it (0 = 32)|
|[24:20]|PUSH_THRESH|`push_thresh`|bits shifted into the ISR before autopush sends it (0 = 32)|
|19|OUT_SHIFTDIR|`out_shift_right`|OUT takes bits from the right (LSB first) or the left|
|18|IN_SHIFTDIR|`in_shift_right`|IN shifts new bits in from the left or the right|
|17|AUTOPULL|`autopull`|refill the OSR from the TX FIFO automatically|
|16|AUTOPUSH|`autopush`|push the ISR to the RX FIFO automatically|

The FIFO-join bits [31:30] aren't supported.

## `pinctrl`: which pins each instruction type touches (datasheet 3.5.6)

| Bits    | Field         | Your wire       | Meaning                                    |
| ------- | ------------- | --------------- | ------------------------------------------ |
| [31:29] | SIDESET_COUNT | `sideset_count` | side-set bits, including the `opt` enable  |
| [28:26] | SET_COUNT     | `set_count`     | pins written by `set pins` (max 5)         |
| [25:20] | OUT_COUNT     | `out_count`     | pins written by `out pins`                 |
| [19:15] | IN_BASE       | `in_base`       | first pin read by `in pins` and `wait pin` |
| [14:10] | SIDESET_BASE  | `sideset_base`  | first side-set pin                         |
| [9:5]   | SET_BASE      | `set_base`      | first SET pin                              |
| [4:0]   | OUT_BASE      | `out_base`      | first OUT pin                              |