#!/bin/bash
cd ~/skeleton && source ~/.bashrc
FLAGS="-O3 -DNDEBUG -pthread -include memory -fpermissive -Wno-changes-meaning -Wno-deprecated-declarations -Wno-return-type -Wno-unused-result -Iinclude -I$TBBROOT/include"
LIBS="-L$TBBLIB -ltbb -lrt -lboost_serialization -Wl,-rpath,$TBBLIB"
U=benchmarks/uts
mpicxx $FLAGS -DNO_VALIDATE --param=large-unit-insns=2000 --param=inline-unit-growth=60 benchmarks/fib_dstack.cpp -o bin/fib_dstack $LIBS &
mpicxx $FLAGS -DNO_VALIDATE benchmarks/NQueens_dstack.cpp -o bin/NQueens_dstack $LIBS &
mpicxx $FLAGS benchmarks/topsorts_dstack.cpp -o bin/topsorts_dstack $LIBS &
mpicxx $FLAGS -DBRG_RNG -DDPARALLEL_STACK_RECURSION -I$U -I$U/rng $U/uts_par_rec.cpp bin/uts_c.o bin/brg_sha1.o -o bin/uts_dstack $LIBS &
wait
ls -l bin/*_dstack
