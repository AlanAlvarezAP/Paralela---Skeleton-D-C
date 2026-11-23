#!/bin/bash
# uso: tmux new -d -s run 'PHASES=hb bash ~/cluster/run_experiments.sh > ~/compare.out 2>&1'
# PHASES: hb (por defecto), extra, smoke, tau, full

set -uo pipefail
cd ~/skeleton
rm -f ~/RUN_DONE ~/EXPECTED_RUNS ~/DONE_RUNS
RC=build_scripts/run_compare.py
HB=build_scripts/run_hb.py; [ -f ~/cluster/run_hb.py ] && HB=~/cluster/run_hb.py
PHASES=${PHASES:-hb}
NHOSTS=$(grep -c . hosts)
NODES_LIST=${NODES_LIST:-$(seq -s, 1 "$NHOSTS")}
has() { [[ ",$PHASES," == *",$1,"* ]]; }

echo "### Start: $(date -u) UTC"
echo "### Commit: $(git rev-parse --short HEAD) ($(git branch --show-current))"
md5sum include/dparallel_recursion/dparallel_stack_recursion.h include/dparallel_recursion/HeartBeat.h
git status --short include benchmarks

if has smoke; then
  echo "### smoke (fib 35, NQueens 12, quicksort 1e7; 1 and 2 nodes; N=30)"
  MODE=smoke TIMEOUT=300 python3 -u $RC 1,2 1
  smoke=$?
  if [ $smoke -ne 0 ]; then
    echo "### SMOKE REPORTED PROBLEMS (list above). Known: quicksort custom with 2 nodes gives FAILURE (pre-existing)."
    if [ "${STRICT_SMOKE:-0}" = "1" ]; then
      tar czf ~/resultados_hb.tar.gz resultados_compare_smoke.csv logs_compare_smoke hosts -C ~ compare.out
      echo 2 > ~/RUN_DONE; exit 1
    fi
  fi
fi

if has tau; then
  echo "### tau (fib 42, 1 thread, N=1 and N=10^7)"
  MODE=tau TIMEOUT=600 python3 -u $RC 1 1
fi

status=0
if has hb; then
  echo "### hb: tau per benchmark, then NODES=$NODES_LIST with each benchmark's N (reps ${HB_REPS:-1})"
  MODE=${HB_MODE:-all} python3 -u $HB "$NODES_LIST" ${HB_REPS:-1}
  status=$?
fi

if has extra; then
  run_part() {
    local desc=$1; shift
    echo "### extra: $desc"
    env -u STACK_KB -u AUTOMATIC -u OUT_TAG -u BASELINES -u TESTS MODE=nodes "$@" \
      python3 -u $HB "$NODES_LIST" "${PART_REPS:-1}"
    local st=$?; [ $st -ne 0 ] && status=$st
  }
  run_part "topsorts simple + automatic" OUT_TAG=topsorts TESTS=topsorts BASELINES=simple N_topsorts=${N_topsorts:-3}
  PART_REPS=${FIB_REPS:-4} run_part "fib x${FIB_REPS:-4}: only automatic N=${N_fib:-1}" \
    OUT_TAG=fib_reps TESTS=fib BASELINES=none N_fib=${N_fib:-1}
  run_part "T3XXL custom with ${STACK_KB:-1048576} KB stack" OUT_TAG=t3xxl_stack TESTS=uts_T3XXL \
    BASELINES=simple,custom_bad,custom_good,custom_many AUTOMATIC=0 STACK_KB=${STACK_KB:-1048576}
fi

if has full; then
  echo "### full: NODES=$NODES_LIST"
  export MODE=full
  rm -f ~/DONE_RUNS
  MODE=full DRY_RUN=1 python3 $RC "$NODES_LIST" ${REPS:-1} 2>/dev/null | head -1 | tee ~/EXPECTED_RUNS
  python3 -u $RC "$NODES_LIST" ${REPS:-1}
  st=$?; [ $st -ne 0 ] && status=$st
fi
echo "### End: $(date -u) UTC  (exit code: $status; 0 = all ok, 1 = see PROBLEMS list)"

for ip in $(cut -d' ' -f1 hosts); do
  echo "== $ip"; ssh -o BatchMode=yes root@$ip "hostname; lscpu | grep -E 'Model name|Thread|Core|Socket|^CPU\(s\)'"
done > lscpu.txt 2>&1

{ git log --oneline -1; git status --short include; \
  md5sum include/dparallel_recursion/dparallel_stack_recursion.h include/dparallel_recursion/HeartBeat.h; } > commit.txt
tar czf ~/resultados_hb.tar.gz $(ls -d resultados_compare_*.csv logs_compare_* resultados_hb_*.csv resumen_hb_nodes*.txt tau_N.json logs_hb_* 2>/dev/null) hosts lscpu.txt commit.txt -C ~ compare.out
echo "$status" > ~/RUN_DONE
echo "### Packed: ~/resultados_hb.tar.gz"
