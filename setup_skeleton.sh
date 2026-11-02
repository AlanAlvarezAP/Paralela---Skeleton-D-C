set -e

REPO_URL=${REPO_URL:-https://github.com/AlanAlvarezAP/Paralela---Skeleton-D-C.git}
SKEL=$HOME/skeleton
TBBROOT=$HOME/tbb2020

echo "### 1/6 Dependencias"
sudo apt update
sudo apt install -y build-essential cmake git bc python3 \
  openmpi-bin libopenmpi-dev libboost-serialization-dev

echo "### 2/6 TBB 2020.3 (la libreria usa la API clasica, no oneTBB)"
[ -d "$TBBROOT" ] || git clone --branch v2020.3 --depth 1 \
  https://github.com/oneapi-src/oneTBB.git "$TBBROOT"
if ! ls "$TBBROOT"/build/*_release/libtbb.so* >/dev/null 2>&1; then
  cd "$TBBROOT"
  make -j"$(nproc)" || {
    make clean
    make -j"$(nproc)" compiler=gcc \
      CXXFLAGS="-Wno-error -Wno-deprecated-declarations -Wno-stringop-overflow -Wno-class-memaccess -fpermissive -Wno-changes-meaning"
  }
fi
TBBLIB=$(ls -d "$TBBROOT"/build/*_release | head -1)
[ -n "$TBBLIB" ] || { echo "ERROR: TBB no compilo"; exit 1; }
export TBBROOT TBBLIB
export CPLUS_INCLUDE_PATH=$TBBROOT/include
export LIBRARY_PATH=$TBBLIB
export LD_LIBRARY_PATH=$TBBLIB

echo "### 3/6 Variables de entorno permanentes (~/.bashrc)"
if ! grep -q "TBB 2020 para dparallel_recursion" ~/.bashrc; then
cat >> ~/.bashrc << 'EOF'

# TBB 2020 para dparallel_recursion
export TBBROOT=$HOME/tbb2020
export TBBLIB=$(ls -d $TBBROOT/build/*_release 2>/dev/null | head -1)
export CPLUS_INCLUDE_PATH=$TBBROOT/include
export LIBRARY_PATH=$TBBLIB
export LD_LIBRARY_PATH=$TBBLIB
EOF
fi

echo "### 4/6 Clonar repo"
[ -d "$SKEL" ] || git clone "$REPO_URL" "$SKEL"
cd "$SKEL"
mkdir -p bin results

echo "### 5/6 Scripts de compilacion y pruebas"

cat > build_all.sh << 'EOF_BUILD'
#!/bin/bash
cd ~/skeleton
: "${TBBROOT:?falta TBBROOT}"; : "${TBBLIB:?falta TBBLIB}"
mkdir -p bin; : > build.log
FLAGS="-O3 -DNDEBUG -pthread -include memory -Wno-deprecated-declarations -Wno-return-type -Wno-unused-result -fpermissive -Wno-changes-meaning -Iinclude -I$TBBROOT/include"
LIBS="-L$TBBLIB -ltbb -lrt -lboost_serialization -Wl,-rpath,$TBBLIB"

for b in fib NQueens topsorts fib_stack NQueens_stack topsorts_stack; do
  mpicxx $FLAGS benchmarks/$b.cpp -o bin/$b $LIBS 2>>build.log && echo "OK $b" || echo "FALLO $b"
done

U=benchmarks/uts
gcc -O3 -DNDEBUG -DBRG_RNG -I$U -I$U/rng -c $U/uts.c -o bin/uts_c.o 2>>build.log
gcc -O3 -DNDEBUG -DBRG_RNG -I$U -I$U/rng -c $U/rng/brg_sha1.c -o bin/brg_sha1.o 2>>build.log
mpicxx $FLAGS -DBRG_RNG -DDPARALLEL_RECURSION -I$U -I$U/rng $U/uts_par_rec.cpp bin/uts_c.o bin/brg_sha1.o -o bin/uts $LIBS 2>>build.log && echo "OK uts" || echo "FALLO uts"
mpicxx $FLAGS -DBRG_RNG -DPARALLEL_STACK_RECURSION -I$U -I$U/rng $U/uts_par_rec.cpp bin/uts_c.o bin/brg_sha1.o -o bin/uts_stack $LIBS 2>>build.log && echo "OK uts_stack" || echo "FALLO uts_stack"
echo "Errores detallados en build.log (busca 'error:')"
EOF_BUILD
chmod +x build_all.sh

cat > run_all.sh << 'EOF_RUN'
#!/bin/bash
# Uso: ./run_all.sh [quick|medium|full]
# Variables: THREADS=8 REPS=3 ./run_all.sh quick
# Nota: 'medium' y 'full' se ejecutan ademas de lo de 'quick' (lo incluyen).
MODE=${1:-quick}
THREADS=${THREADS:-$(nproc)}
REPS=${REPS:-3}
export OMP_NUM_THREADS=$THREADS

cd ~/skeleton
source sequential/inputs/uts/sample_trees.sh
OUT=results/$(date +%Y%m%d_%H%M)_${MODE}_t${THREADS}
mkdir -p $OUT/logs
CSV=$OUT/tiempos.csv
echo "prueba,rep,hilos,codigo_salida,segundos_wall,salida_del_programa" > $CSV
echo "Resultados en: $OUT"

# run ETIQUETA TIMEOUT ENTRADA(o -) comando...
# codigo_salida 124 = cortado por timeout. Usa el tiempo de "compute time",
# el wall incluye la verificacion secuencial del programa.
run() {
  local label=$1 tmo=$2 inp=$3; shift 3
  for i in $(seq 1 $REPS); do
    local log=$OUT/logs/${label}_r$i.log
    local t0=$(date +%s.%N)
    if [ "$inp" = "-" ]; then timeout $tmo "$@" > $log 2>&1
    else timeout $tmo "$@" < $inp > $log 2>&1; fi
    local rc=$?
    local t1=$(date +%s.%N)
    local wall=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.3f", b-a}')
    local prog=$(grep -iE "compute time|^Time:|wallclock" $log | head -1 | tr ',' ';')
    echo "$label,$i,$THREADS,$rc,$wall,$prog" >> $CSV
    echo "  $label r$i  rc=$rc  ${wall}s  $prog"
    [ $rc -eq 124 ] && break
  done
}

IN=sequential/inputs/topsorts

# ---------- Fibonacci ----------
for n in 40 45; do
  run fib_stack_n$n 300 - ./bin/fib_stack $n
  run fib_mpi_n$n   300 - mpirun -np 1 ./bin/fib $n
done

# ---------- N-Queens ----------
for n in 14 15 16; do
  run nqueens_stack_n$n 600 - ./bin/NQueens_stack $n
  run nqueens_mpi_n$n   600 - mpirun -np 1 ./bin/NQueens $n
done

# ---------- Topsorts ----------
for f in pm10 pm16 pm18 pm20; do
  run topsorts_stack_$f 600 $IN/$f.txt ./bin/topsorts_stack
  run topsorts_mpi_$f   600 $IN/$f.txt mpirun -np 1 ./bin/topsorts
done

# ---------- UTS (chicos, ~4M a ~110M nodos) ----------
for t in T1 T2 T3 T1L T2L T3L; do
  run uts_stack_$t 600 - ./bin/uts_stack ${!t}
  run uts_mpi_$t   600 - mpirun -np 1 ./bin/uts ${!t}
done

if [ "$MODE" = "medium" ]; then
  run fib_stack_n47       600 - ./bin/fib_stack 47
  run nqueens_stack_n17   600 - ./bin/NQueens_stack 17
  run nqueens_mpi_n17     900 - mpirun -np 1 ./bin/NQueens 17
  run topsorts_stack_pm22 600 $IN/pm22.txt ./bin/topsorts_stack
fi

if [ "$MODE" = "full" ]; then
  run fib_stack_n47      900 - ./bin/fib_stack 47
  run nqueens_stack_n17  1200 - ./bin/NQueens_stack 17
  for f in pm22 cat42 k89; do
    run topsorts_stack_$f 1200 $IN/$f.txt ./bin/topsorts_stack
  done
  # UTS del paper: T2XL (~1.5 mil millones de nodos) y T3XXL (~2.8 mil millones)
  run uts_stack_T2XL   1800 - ./bin/uts_stack $T2XL
  run uts_stack_T3XXL  3600 - ./bin/uts_stack $T3XXL
fi

echo; echo "Listo. Tabla: $CSV"
column -s, -t $CSV | cut -c1-140
EOF_RUN
chmod +x run_all.sh

cat > sweep_np.sh << 'EOF_SWEEP'
#!/bin/bash
# Barrido de procesos MPI en una misma maquina, manteniendo np x hilos = nproc.
# Solo los binarios distribuidos (NQueens, uts, topsorts); los *_stack son memoria compartida.
# Uso: ./sweep_np.sh "1 2 3 4 6"
cd ~/skeleton
source sequential/inputs/uts/sample_trees.sh
TOTAL=$(nproc)
NPS=${1:-"1 2 3 4 6"}
mkdir -p results
OUT=results/np_sweep_$(date +%Y%m%d_%H%M).txt
for NP in $NPS; do
  T=$((TOTAL/NP))
  echo "=== np=$NP hilos=$T"
  for cmd in "./bin/NQueens 16" "./bin/uts $T2L" "./bin/uts $T3L"; do
    OMP_NUM_THREADS=$T mpirun -np $NP --oversubscribe $cmd 2>&1 \
      | grep -E "Nprocs|compute time|Wallclock" | tr '\n' ' '; echo
  done
  OMP_NUM_THREADS=$T mpirun -np $NP --oversubscribe ./bin/topsorts \
      < sequential/inputs/topsorts/pm20.txt 2>&1 | grep -E "Nprocs|Time:" | tr '\n' ' '; echo
done | tee $OUT
echo "Guardado en $OUT"
EOF_SWEEP
chmod +x sweep_np.sh

echo "### 6/6 Compilar benchmarks y tests"
./build_all.sh

mkdir -p build && cd build
cmake -DCMAKE_CXX_FLAGS="-fpermissive -Wno-changes-meaning" -DONLY_THREADS=1 -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_STANDARD_LIBRARIES="-L$TBBLIB -ltbb -pthread" \
  -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath,$TBBLIB" .. > /dev/null 2>&1 \
  && make -j"$(nproc)" 2>&1 | tail -3 \
  || echo "AVISO: fallo la compilacion de tests (los benchmarks no dependen de esto)"
cd "$SKEL"

echo
echo "============================================================"
echo " Listo. Abre una terminal nueva (o: source ~/.bashrc) y usa:"
echo "   cd ~/skeleton"
echo "   ./build/tests/test_local        # test base"
echo "   REPS=1 ./run_all.sh quick       # ~7 min con 12 hilos"
echo "   REPS=1 ./run_all.sh medium      # quick + pruebas un poco mas grandes"
echo "   ./sweep_np.sh \"1 2 3 4 6\"       # barrido de procesos MPI"
echo "============================================================"