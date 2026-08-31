* Hierarchical passive filter using only public textbook components
.include "cells/passive stages.sp"
.param drive=1
Vinput in 0 DC {drive}
Xfilter in out two_stage SCALE=2
Rload out 0 10k
.end
