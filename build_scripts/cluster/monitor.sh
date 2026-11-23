#!/bin/bash
# monitor.sh - runs on n1: progress of run_experiments.sh and CPU of the benchmark on every node.
# usage (from the PC, refresh every 5 s): ssh -t root@N1 "watch -n 5 bash ~/cluster/monitor.sh"
CSV=$(ls -t ~/skeleton/resultados_compare_*.csv ~/skeleton/resultados_hb_*.csv 2>/dev/null | head -1)
total=$(grep -o '[0-9]* runs' ~/EXPECTED_RUNS 2>/dev/null | cut -d' ' -f1)
done_runs=0
[ -n "$CSV" ] && done_runs=$(( $(wc -l < "$CSV") - 1 ))
# run_hb.py counts tau + nodes together in ~/DONE_RUNS (the CSV only has the current phase)
[ -f ~/DONE_RUNS ] && done_runs=$(cat ~/DONE_RUNS)
echo "$(basename "${CSV:-none}"): $done_runs / ${total:-?} runs   $(date -u +%H:%M:%S) UTC   RUN_DONE: $(cat ~/RUN_DONE 2>/dev/null || echo running)"
echo
if [ -n "$CSV" ] && head -1 "$CSV" | grep -q '^test,'; then
  echo "Last runs (tau phase: test N time_s promotions result state):"
  tail -n 4 "$CSV" | awk -F, '$1!="test" {printf "  %-10s %-11s %-9s %-10s %-13s %s\n", $1,$3,$6,$8,$9,$10}'
else
  echo "Last runs (nodes test config cutoff N time_s promotions result state):"
  [ -n "$CSV" ] && tail -n 4 "$CSV" | awk -F, '$1!="nodos" {printf "  %s %-10s %-15s %-7s %-5s %-9s %-10s %-13s %s\n", $1,$3,$5,$7,$8,$12,$13,$14,$15}'
fi
echo
echo "CPU per node (800% = all 8 vCPUs):"
for ip in $(cut -d' ' -f1 ~/skeleton/hosts); do
  line=$(ssh -o BatchMode=yes -o ConnectTimeout=3 root@$ip "hostname; top -bn1 -w 300 -c | grep -E 'bin/[A-Za-z_]+_dstack' | grep -v grep | head -1" 2>/dev/null)
  name=$(echo "$line" | head -1)
  proc=$(echo "$line" | sed -n 2p | awk '{printf "%5s%%  %s", $9, $12; for (i=13; i<=NF && i<=20; i++) printf " %s", $i}')
  printf "  %-4s %s\n" "$name" "${proc:-   idle}"
done
