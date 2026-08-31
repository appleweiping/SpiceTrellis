* One parameterized RC section
.subckt passive_stage input output RVAL=1k CVAL=1n
Rseries input output {RVAL}
Cshunt output 0 {CVAL}
.ends passive_stage

* A nested two-section filter
.subckt two_stage input output SCALE=1
Xfirst input middle passive_stage RVAL={1k*SCALE} CVAL=1n
Xsecond middle output passive_stage RVAL={2k*SCALE} CVAL=2n
.ends two_stage
