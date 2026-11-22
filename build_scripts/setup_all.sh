#!/bin/bash
# setup_todo.sh - instala dependencias, compila TBB y compila los benchmarks *_dstack (4 + quicksort_dstack_gatherinput).
# (junta setup_skeleton.sh + build_dstack.sh; ya no genera build_all/run_all/sweep ni tests)
set -e

REPO_URL=${REPO_URL:-https://github.com/AlanAlvarezAP/Paralela---Skeleton-D-C.git}
BRANCH=${BRANCH:-}
SKEL=$HOME/skeleton
TBBROOT=$HOME/tbb2020

echo "### 1/5 Dependencias"
sudo apt update
sudo apt install -y build-essential cmake git bc python3 \
  openmpi-bin libopenmpi-dev libboost-serialization-dev

echo "### 2/5 TBB 2020.3 (la libreria usa la API clasica, no oneTBB)"
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

echo "### 3/5 Variables de entorno permanentes (~/.bashrc)"
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

echo "### 4/5 Clonar repo"
[ -d "$SKEL" ] || git clone ${BRANCH:+--branch "$BRANCH"} "$REPO_URL" "$SKEL"
cd "$SKEL"
if [ -n "$BRANCH" ]; then
  git fetch origin
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi
mkdir -p bin results

echo "### 5/5 Compilar los benchmarks dstack"
FLAGS="-O3 -DNDEBUG -pthread -include memory -fpermissive -Wno-changes-meaning -Wno-deprecated-declarations -Wno-return-type -Wno-unused-result -Iinclude -I$TBBROOT/include"
LIBS="-L$TBBLIB -ltbb -lrt -lboost_serialization -Wl,-rpath,$TBBLIB"
U=benchmarks/uts

# UTS necesita estos dos .o (codigo C del generador de numeros aleatorios)
gcc -O3 -DNDEBUG -DBRG_RNG -I$U -I$U/rng -c $U/uts.c -o bin/uts_c.o
gcc -O3 -DNDEBUG -DBRG_RNG -I$U -I$U/rng -c $U/rng/brg_sha1.c -o bin/brg_sha1.o

# Los 4 en paralelo (&) y esperamos a que terminen (wait)
mpicxx $FLAGS -DNO_VALIDATE --param=large-unit-insns=2000 --param=inline-unit-growth=60 benchmarks/fib_dstack.cpp -o bin/fib_dstack $LIBS &
mpicxx $FLAGS -DNO_VALIDATE benchmarks/NQueens_dstack.cpp -o bin/NQueens_dstack $LIBS &
mpicxx $FLAGS benchmarks/topsorts_dstack.cpp -o bin/topsorts_dstack $LIBS &
mpicxx $FLAGS -DBRG_RNG -DDPARALLEL_STACK_RECURSION -I$U -I$U/rng $U/uts_par_rec.cpp bin/uts_c.o bin/brg_sha1.o -o bin/uts_dstack $LIBS &
mpicxx $FLAGS benchmarks/quicksort_dstack_gatherinput.cpp -o bin/quicksort_dstack_gatherinput $LIBS &
wait

ls -l bin/*_dstack
echo "Listo. Abre una terminal nueva (o: source ~/.bashrc)."