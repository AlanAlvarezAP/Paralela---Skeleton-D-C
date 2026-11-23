# build_scripts/cluster — Heartbeat experiments on a multi-node cluster

Scripts that set up N nodes (tested with 4 Scaleway POP2 instances, 8 vCPU, Ubuntu 24.04) and run the
Heartbeat experiments with `build_scripts/run_hb.py`. They only automate the setup; the build is
`build_scripts/setup_all.sh` and the measurement is `run_hb.py`.

| File | Runs on | What it does |
|---|---|---|
| `deploy.sh` | n1 | Installs the cluster key, sends `setup_all.sh` (and `hb_fix.patch` if it is next to it) to every node and runs it in parallel (deps, TBB 2020, clone `BRANCH`, build the 5 binaries: fib, NQueens, uts, topsorts, quicksort_gather), checks that every node has the same commit and the same md5 of `HeartBeat.h`, writes `~/skeleton/hosts` and tests `mpirun`. Ends with `DEPLOY OK`. |
| `run_experiments.sh` | n1, inside tmux | Default phase `hb` = `run_hb.py` (tau, then nodes). Packs `~/resultados_hb.tar.gz` and writes the exit code to `~/RUN_DONE`. |
| `monitor.sh` | n1 | Progress (`done / total` of the whole run), last rows of the CSV and benchmark CPU on every node. |
| `nodes.txt.example` | — | Template for `nodes.txt`: one public IP per line, n1 first. |

Not in git (see `.gitignore`): `nodes.txt` (real IPs) and `id_cluster` (private key that opens every node).

## Experiment design (default: 24 runs)

Benchmarks: `fib`, `uts_T3XXL`, `uts_T2XL`, `topsorts`. Only the `automatic` partitioner.

**Phase tau — 4 benchmarks × 2 runs = 8 runs.** 1 node, 1 thread, `DPR_HB_CHECK_EVERY=1`, reduced
input of the same family. For each benchmark: one run with N = 10^9 µs (0 promotions, time T) and one
with N = 1 µs (C promotions, time T′). Then tau = (T′ − T) / C and **N = ceil(20 · tau)**, saved in
`~/skeleton/tau_N.json`. tau is measured with 1 thread on purpose: with more threads T′ gets the
parallelism and T does not, so T′ < T and tau would be negative.

| Benchmark | Input of phase nodes | Input of phase tau |
|---|---|---|
| fib | fib(52) | fib(42) |
| uts_T3XXL | `-t 0 -b 2000 -q 0.499995 -m 2 -r 316` | `-q 0.4999` |
| uts_T2XL | `-t 1 -a 2 -d 26 -b 7 -r 220` | `-d 20` |
| topsorts | pm22 | pm18 |

**Phase nodes — 4 benchmarks × 4 node counts = 16 runs.** 1, 2, 3 and 4 nodes, 8 threads per node,
`DPR_HB_CHECK_EVERY=128`, 1 rep. Every benchmark uses **its own N** (the one from its tau) on the 4 node counts.

| Variant | Tau | Nodes | Total |
|---|---|---|---|
| Default | 8 | 16 | **24** |
| `TAU_REPS=2` (median of 2 in tau) | 16 | 16 | 32 |

Check without running anything: `DRY_RUN=1 python3 build_scripts/run_hb.py 1,2,3,4` (prints the 24 commands
and how the nodes are shared).

### Simultaneous runs on different nodes (`PACK=1`, default)

Runs that use different nodes run at the same time; **a node is never shared by two runs**.

- Tau: each benchmark on its own node (its T and T′ on that same node, one after the other), the 4 at once.
- Nodes: first the 4-node runs (one at a time), then each 3-node run together with a 1-node run on the
  free node, the 2-node runs two at a time, and so on (greedy: the first run in the list that fits in the free nodes).
- Each CSV row keeps `hosts`, `inicio_s` and `fin_s` (seconds since the phase started), so the overlap can be checked.
- Caveat for the report: simultaneous runs share the cloud network (not CPUs or memory). `PACK=0` runs
  one at a time on the first nodes, as before.

## Steps (PowerShell on the PC; `$K` = your private key, `$N1` = IP of n1)

```powershell
# 1. local folder to upload: these scripts + setup_all.sh + run_hb.py + nodes.txt + id_cluster
mkdir cluster; Copy-Item build_scripts\cluster\*.sh, build_scripts\setup_all.sh, build_scripts\run_hb.py cluster\
$IPS | Out-File -Encoding ascii cluster\nodes.txt      # 4 IPs, n1 first
Copy-Item $K cluster\id_cluster
scp -i $K -r .\cluster "root@${N1}:~/"

# 2. deploy (10-15 min). Wait for "DEPLOY OK" in the log
ssh -i $K root@$N1 "tmux new -d -s deploy 'BRANCH=develop bash ~/cluster/deploy.sh > ~/deploy.log 2>&1'"
ssh -i $K root@$N1 "tail -15 ~/deploy.log"

# 3. experiments: 8 + 16 = 24 runs
ssh -i $K root@$N1 "tmux new -d -s run 'bash ~/cluster/run_experiments.sh > ~/compare.out 2>&1'"
ssh -t -i $K root@$N1 "watch -n 5 bash ~/cluster/monitor.sh"

# 4. when ~/RUN_DONE exists: download
scp -i $K "root@${N1}:~/resultados_hb.tar.gz" .
```

`run_experiments.sh` uses `~/cluster/run_hb.py` if it was uploaded, otherwise the one of the cloned branch.
Then delete the instances (with their IPs and volumes) so they stop billing.

## Options

| Variable | Default | Meaning |
|---|---|---|
| `PHASES` | `hb` | also `smoke`, `tau`, `full` (old `run_compare.py` campaign with simple/custom) |
| `HB_MODE` | `all` | `tau`, `nodes` (reuses `tau_N.json`) or `all` |
| `HB_REPS` | `1` | reps of phase nodes |
| `PACK` | `1` | `1`: runs on different nodes at the same time; `0`: one run at a time |
| `NODES_LIST` | `1..lines of hosts` | node counts of phase nodes |
| `TAU_REPS` | `1` | reps of each tau run (median) |
| `N_fib`, `N_uts_T3XXL`, `N_uts_T2XL`, `N_topsorts` | — | force the N of a benchmark instead of its tau |
| `BASELINES` | `none` | e.g. `simple` or `simple,custom_good` to also run baselines (more runs) |
| `TESTS`, `K`, `TAU_CHECK`, `HB_CHECK_EVERY`, `FIB_N`, `TOPSORTS_INPUT`, `TIMEOUT` | see `run_hb.py` | |

## Results

- `tau_N.json`, `resultados_hb_tau.csv`, `logs_hb_tau/`: T, T′, C, tau and N of every benchmark.
- `resultados_hb_nodes.csv`, `resumen_hb_nodes.txt`, `logs_hb_nodes/`: the 16 runs and speedup vs 1 node.
- Everything packed in `~/resultados_hb.tar.gz` with `commit.txt` (commit + md5 of the headers) and `lscpu.txt`.

If a tau looks wrong (the log shows `AVISO` or `(RESPALDO)`, N_FALLBACK = 30 µs), repeat with
`TAU_REPS=3` or force the N with `N_<test>=..`.

## Known issues

- `pre` applied twice: an older `HeartBeat.h` called `body.pre` on a child when it was generated, and again
  when that child was promoted and stolen. topsorts (`pre` does `push_back`) gave wrong counts or hung.
  **Fixed in `develop` (commit 1676797)**: `pre` runs once, when the node is popped. So no `hb_fix.patch` is
  needed with `BRANCH=develop`; `setup_all.sh` only applies a patch if `hb_fix.patch` is next to it, and aborts
  if it does not apply.
- `mpirun` must keep `--bind-to none` (in `run_hb.py`); without it, 1 process per node pins all its threads to one core.
