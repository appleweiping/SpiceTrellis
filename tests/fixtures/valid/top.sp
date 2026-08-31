.include "library/passives.sp"
.param supply=1.2 gain=2
VDD vdd 0 DC {supply}
X1 vin vout divider RUP={5k*gain} RDOWN=5k
Cload vout 0 2p
.end
