#!/bin/bash
# run_experiments.sh - runs on n1 inside tmux, after deploy.sh. Phases smoke -> hb (and optionally the old
# tau / full), then packs everything into ~/resultados_hb.tar.gz and writes the exit code to ~/RUN_DONE.
#   hb = build_scripts/run_hb.py: tau of fib, uts_T3XXL, uts_T2XL and topsorts -> N = ceil(20 tau) for each,
#        then only automatic with that N on 1..4 nodes. See run_hb.py for its env (K, TAU_REPS, BASELINES...).
#        Default with 4 nodes: tau 4 tests x 2 = 8 runs + nodes 4 tests x 4 node counts = 16 runs = 24 runs.
# usage: tmux new -d -s run 'bash ~/cluster/run_experiments.sh > ~/compare.out 2>&1'
# env (all optional):
#   PHASES=hb               phases to run (also available: smoke, tau, full = old run_compare.py campaign)
#   HB_MODE=all             tau | nodes | all for the hb phase (nodes reuses ~/skeleton/tau_N.json)
#   HB_REPS=1               reps of the hb node sweep
#   STRICT_SMOKE=1          stop if smoke reports problems (default: warn and go on; see README, known issues)
#   NODES_LIST=1,2,3,4      node counts of the full phase (default: 1..number of lines in ~/skeleton/hosts)
#   TESTS, HB_PERIODS, FIB_N, NQUEENS_N, TIMEOUT, REPS   passed to the full phase
set -uo pipefail
cd ~/skeleton
rm -f ~/RUN_DONE ~/EXPECTED_RUNS ~/DONE_RUNS
RC=build_scripts/run_compare.py
# run_hb.py: the one in the repo, or the one uploaded next to this script (~/cluster) if it is not pushed yet
HB=build_scripts/run_hb.py; [ -f ~/cluster/run_hb.py ] && HB=~/cluster/run_hb.py
PHASES=${PHASES:-hb}
NHOSTS=$(grep -c . hosts)
NODES_LIST=${NODES_LIST:-$(seq -s, 1 "$NHOSTS")}
has() { [[ ",$PHASES," == *",$1,"* ]]; }

echo "### Start: $(date -u) UTC"
echo "### Commit: $(git rev-parse --short HEAD) ($(git branch --show-current))"
md5sum include/dparallel_recursion/dparallel_stack_recursion.h include/dparallel_recursion/HeartBeat.h
git status --short include benchmarks   # muestra si hb_fix.patch esta aplicado

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

if has full; then
  echo "### full: NODES=$NODES_LIST"
  export MODE=full
  rm -f ~/DONE_RUNS   # run_compare.py does not write it: monitor.sh counts the CSV rows instead
  MODE=full DRY_RUN=1 python3 $RC "$NODES_LIST" ${REPS:-1} 2>/dev/null | head -1 | tee ~/EXPECTED_RUNS
  python3 -u $RC "$NODES_LIST" ${REPS:-1}
  st=$?; [ $st -ne 0 ] && status=$st
fi
echo "### End: $(date -u) UTC  (exit code: $status; 0 = all ok, 1 = see PROBLEMS list)"

# CPU of every node, for the report
for ip in $(cut -d' ' -f1 hosts); do
  echo "== $ip"; ssh -o BatchMode=yes root@$ip "hostname; lscpu | grep -E 'Model name|Thread|Core|Socket|^CPU\(s\)'"
done > lscpu.txt 2>&1

{ git log --oneline -1; git status --short include; \
  md5sum include/dparallel_recursion/dparallel_stack_recursion.h include/dparallel_recursion/HeartBeat.h; } > commit.txt
tar czf ~/resultados_hb.tar.gz $(ls -d resultados_compare_*.csv logs_compare_* resultados_hb_*.csv resumen_hb_nodes.txt tau_N.json logs_hb_* 2>/dev/null) hosts lscpu.txt commit.txt -C ~ compare.out
echo "$status" > ~/RUN_DONE
echo "### Packed: ~/resultados_hb.tar.gz"
