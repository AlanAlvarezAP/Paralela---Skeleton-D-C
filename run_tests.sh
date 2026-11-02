cd ~/skeleton || exit 1
source sequential/inputs/uts/sample_trees.sh
L_T3=${1:-0}; L_T2=${2:-0}; NQ=${3:-0}; FIB=${4:-0}; L_TOP=${5:-0}
NODES=${NODES:-"1"}; CORES=${CORES:-8}; REPS=${REPS:-1}; TMO=${TMO:-900}
BUDGET_MIN=${BUDGET_MIN:-0}; CFGS=${CFGS:-"hibrido mpi_puro"}; HOSTS=${HOSTS:-hosts}
T3_LAD=(T3 T3L T3XXL); T2_LAD=(T2 T2L T2XL)
TOP_LAD=(pm10 pm16 pm18 pm20 pm22 cat42 k89)
die() { echo "ERROR: $*" >&2; exit 1; }
items=(); add() { items+=("$1|$2|$3"); }
add_uts() {
  local -n lad=$1; local lv=$2
  [ "$lv" -eq 0 ] && return
  [ "$lv" -ge 1 ] && [ "$lv" -le "${#lad[@]}" ] || die "nivel $lv invalido (1..${#lad[@]})"
  local t=${lad[$((lv-1))]}
  [ -n "${!t}" ] || die "arbol $t no esta en sample_trees.sh"
  add "uts_$t" "-" "./bin/uts ${!t}"
}
add_uts T3_LAD $L_T3
add_uts T2_LAD $L_T2
[ "$NQ" -gt 0 ]  && add "NQueens_$NQ" "-" "./bin/NQueens $NQ"
[ "$FIB" -gt 0 ] && add "fib_$FIB" "-" "./bin/fib $FIB"
if [ "$L_TOP" -gt 0 ]; then
  [ "$L_TOP" -le "${#TOP_LAD[@]}" ] || die "nivel topsorts invalido (1..${#TOP_LAD[@]})"
  f=${TOP_LAD[$((L_TOP-1))]}
  add "topsorts_$f" "sequential/inputs/topsorts/$f.txt" "./bin/topsorts"
fi
[ ${#items[@]} -gt 0 ] || die "no hay tests: pasa 5 argumentos (0 = saltar)"
[ -f "$HOSTS" ] || echo "localhost slots=$CORES" > "$HOSTS"

OUT=results/run_$(date +%Y%m%d_%H%M); mkdir -p $OUT/logs
CSV=$OUT/tiempos.csv
echo "nodos,config,np,hilos,test,rep,rc,wall_s,resumen" > $CSV
MCA="--allow-run-as-root --bind-to none --mca btl_tcp_if_include 10.0.0.0/24 -x OMP_NUM_THREADS"

run_cfg() {
  local cfg=$1 np=$2 th=$3 hf=$4 map=$5 item name inp cmd r
  echo "=== nodos=$N $cfg np=$np hilos=$th"
  for item in "${items[@]}"; do
    IFS='|' read -r name inp cmd <<< "$item"
    [ "$inp" = "-" ] && inp=/dev/null
    for r in $(seq 1 $REPS); do
      if [ "$BUDGET_MIN" -gt 0 ] && [ $SECONDS -gt $((BUDGET_MIN*60)) ]; then
        echo "  $name: OMITIDO (presupuesto de ${BUDGET_MIN} min agotado)"; continue
      fi
      local log=$OUT/logs/${name}_n${N}_${cfg}_r$r.log
      local t0=$(date +%s.%N)
      OMP_NUM_THREADS=$th timeout -k 10 $TMO mpirun -np $np --hostfile $hf \
        --map-by $map $MCA $cmd < $inp > $log 2>&1
      local rc=$?
      local t1=$(date +%s.%N)
      local wall=$(awk -v a=$t0 -v b=$t1 'BEGIN{printf "%.1f", b-a}')
      local res=$(grep -iE "nprocs|compute time|^time:|wallclock" $log | head -3 | tr '\n' ' ' | tr ',' ';')
      echo "$N,$cfg,$np,$th,$name,$r,$rc,$wall,$res" >> $CSV
      echo "  $name r$r rc=$rc wall=${wall}s  $res"
    done
  done
}

for N in $NODES; do
  [ "$(grep -c . $HOSTS)" -ge "$N" ] || die "$HOSTS tiene menos de $N lineas"
  head -n $N $HOSTS > $OUT/hosts_$N; hf=$OUT/hosts_$N
  for c in $CFGS; do
    case $c in
      hibrido)  run_cfg hibrido  $N           $CORES       $hf ppr:1:node;;
      mixto)    run_cfg mixto    $((2*N))     $((CORES/2)) $hf ppr:2:node;;
      mpi_puro) run_cfg mpi_puro $((N*CORES)) 1            $hf slot;;
    esac
  done
done
echo; echo "Listo: $CSV"; column -s, -t $CSV | cut -c1-150