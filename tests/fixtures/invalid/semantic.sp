.param a={b+1} b={a+1}
.subckt cell p n
Rdup p n 1k
Rdup p n 2k
.ends wrong_name
Xbad only_one cell
Xunknown a b nowhere
.end
