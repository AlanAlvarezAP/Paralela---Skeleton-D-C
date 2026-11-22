"""
Comparison runs for the report: simple vs custom (several cutoffs) vs automatic (heartbeat),
with a sweep of the heartbeat period N (and optionally of check_every).

Same environment as run.py (run setup_all.sh first, ~/skeleton with bin/ and the "hosts" file).

Usage:
    python3 build_scripts/run_compare.py [nodes] [reps]
        nodes : comma separated list of node counts, like run.py (default "1")
        reps  : repetitions of each configuration (default 3; 1 in smoke and tau modes)

Modes (environment variable MODE):
    MODE=smoke  small problem sizes, a few seconds per run. Run it FIRST on the cluster, e.g.
                MODE=smoke python3 build_scripts/run_compare.py 1,2
                It checks that every configuration gives the same result, that quicksort
                (GatherInput) sorts correctly and that automatic makes promotions.
    MODE=full   (default) the real measurements, same sizes as run.py.
    MODE=tau    experiment of Heartbeat paper section 5.1 to measure the cost tau of one promotion:
                1 thread, automatic with N=1 and N=10^7 (practically no heartbeat):
                tau = (T(N=1) - T(N=10^7)) / promotions(N=1). Suggested default N = 20 * tau.
                Use it with 1 node: MODE=tau python3 build_scripts/run_compare.py 1 3

Optional environment variables:
    TESTS=fib,NQueens            only run these tests (fib, NQueens, uts_T3XXL, uts_T2XL, topsorts, quicksort_gather)
    HB_PERIODS=1,10,30,100       values of N (microseconds) for automatic
    HB_CHECK_EVERY=64,128,256    values of check_every for automatic
    FIB_N=57 NQUEENS_N=17 QS_N=100000000   problem sizes (override the ones of the mode)
    N_THREADS=8                  threads per process (OMP_NUM_THREADS; 1 in tau mode)
    TIMEOUT=3600                 seconds before a run is killed (a hang is recorded as TIMEOUT)
    DRY_RUN=1                    only print the commands, run nothing

Output (with the mode in the name, so the modes do not overwrite each other):
    resultados_compare_<mode>.csv  one row per run
    logs_compare_<mode>/           full output of each run

CSV columns:
    nodos, threads, test, size, config, partitioner, cutoff, hb_period_us, hb_check_every, rep,
    tiempo_wall_s  (whole mpirun, includes MPI start-up)
    tiempo_bench_s (time printed by the benchmark itself: use this one for the plots)
    promociones    (sum of the heartbeat promotions of all threads, only automatic)
    resultado      (result printed by the benchmark; quicksort: SUCCESS/FAILURE)
    estado         (ok / error code / TIMEOUT)
"""

import csv
import os
import re
import subprocess
import sys
import time

MODE = os.environ.get("MODE", "full")
if MODE not in ("full", "smoke", "tau"):
    sys.exit("MODE must be full, smoke or tau")


def env_list(name, default):
    value = os.environ.get(name)
    return [int(x) for x in value.split(",")] if value else default


DRY_RUN = os.environ.get("DRY_RUN") == "1"
TIMEOUT = int(os.environ.get("TIMEOUT", "3600"))

# defaults of each mode: threads, reps, N values, tests, problem sizes
if MODE == "full":
    N_THREADS, REPS_DEFAULT = 8, 3
    PERIODS_DEFAULT = [5, 10, 30, 100, 1000]
    TESTS_DEFAULT = ["fib", "NQueens", "uts_T3XXL", "uts_T2XL", "topsorts", "quicksort_gather"]
    SIZES = {"fib": 57, "NQueens": 17, "quicksort_gather": 100000000}
elif MODE == "smoke":
    N_THREADS, REPS_DEFAULT = 4, 1
    PERIODS_DEFAULT = [30]
    TESTS_DEFAULT = ["fib", "NQueens", "quicksort_gather"]
    SIZES = {"fib": 35, "NQueens": 12, "quicksort_gather": 10000000}
else:  # tau
    N_THREADS, REPS_DEFAULT = 1, 1
    PERIODS_DEFAULT = [1, 10000000]
    TESTS_DEFAULT = ["fib"]
    SIZES = {"fib": 42, "NQueens": 14, "quicksort_gather": 10000000}

N_THREADS = int(os.environ.get("N_THREADS", N_THREADS))
HB_PERIODS = env_list("HB_PERIODS", PERIODS_DEFAULT)
HB_CHECK_EVERY = env_list("HB_CHECK_EVERY", [128])
SIZES["fib"] = int(os.environ.get("FIB_N", SIZES["fib"]))
SIZES["NQueens"] = int(os.environ.get("NQUEENS_N", SIZES["NQueens"]))
SIZES["quicksort_gather"] = int(os.environ.get("QS_N", SIZES["quicksort_gather"]))

nodes_list = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "1").split(",")]
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else REPS_DEFAULT

T3XXL = "-t 0 -b 2000 -q 0.499995 -m 2 -r 316"
T2XL = "-t 1 -a 2 -d 26 -b 7 -r 220"


def opt(c, fmt):
    return "" if c is None else fmt % c


# Each test: name, size, command builder, stdin file, partitioner codes, custom cutoffs.
#   cmd(p, cutoff): p is the code of the partitioner for that benchmark, cutoff only for custom
#   codes: {"simple": x, "custom": y, "automatic": z} (they are different in each benchmark)
#   cutoffs: (value, label). The labels are a first guess; the best cutoff is the one with the
#   lowest time in the data. Add or change values freely.
ALL_TESTS = [
    {
        "name": "fib", "size": SIZES["fib"],
        # argv: n chunkSize chunksToSteal pollingInterval threads_request_policy stackSize partitioner [limitParallel]
        # custom: do_parallel(n) = n > limitParallel
        "cmd": lambda p, c: "./bin/fib_dstack %d 4 1 0 0 100 %d%s" % (SIZES["fib"], p, opt(c, " %d")),
        "stdin": None,
        "codes": {"simple": 0, "custom": 1, "automatic": 2},
        "cutoffs": [(7, "custom_default"),   # default of the benchmark (what run.py measured)
                    (SIZES["fib"] - 27, "custom_mid"),
                    (SIZES["fib"] - 17, "custom_good"),   # ~2500 parallel tasks
                    (SIZES["fib"] - 5, "custom_bad")],    # ~8 parallel tasks: not enough for all the threads
    },
    {
        "name": "NQueens", "size": SIZES["NQueens"],
        # argv: n chunkSize chunksToSteal pollingInterval threads_request_policy stackSize partitioner [limitParallel]
        # custom: parallel while the number of tasks < nthreads * limitParallel
        "cmd": lambda p, c: "./bin/NQueens_dstack %d 8 1 0 0 100 %d%s" % (SIZES["NQueens"], p, opt(c, " %d")),
        "stdin": None,
        "codes": {"simple": 0, "custom": 1, "automatic": 2},
        "cutoffs": [(1, "custom_bad"),        # one task per thread: bad balance
                    (768, "custom_good"),     # default of the benchmark
                    (100000, "custom_many")], # too many parallel tasks: overhead
    },
    {
        "name": "uts_T3XXL", "size": "T3XXL",
        # -P partitioner (0 custom, 1 simple, 2 automatic), -U height_to_serial (custom: parallel while height < U)
        "cmd": lambda p, c: "./bin/uts_dstack %s -P %d%s" % (T3XXL, p, opt(c, " -U %d")),
        "stdin": None,
        "codes": {"simple": 1, "custom": 0, "automatic": 2},
        "cutoffs": [(1, "custom_bad"), (5, "custom_good"), (15, "custom_many")],
    },
    {
        "name": "uts_T2XL", "size": "T2XL",
        "cmd": lambda p, c: "./bin/uts_dstack %s -P %d%s" % (T2XL, p, opt(c, " -U %d")),
        "stdin": None,
        "codes": {"simple": 1, "custom": 0, "automatic": 2},
        "cutoffs": [(1, "custom_bad"), (5, "custom_good"), (15, "custom_many")],
    },
    {
        "name": "topsorts", "size": "cat42",
        # argv: chunkSize chunksToSteal pollingInterval threads_request_policy stackSize partitioner
        # this benchmark has no custom partitioner (only simple and automatic)
        "cmd": lambda p, c: "./bin/topsorts_dstack 4 1 0 0 100 %d" % p,
        "stdin": "sequential/inputs/topsorts/cat42.txt",
        "codes": {"simple": 0, "automatic": 2},
        "cutoffs": [],
    },
    {
        "name": "quicksort_gather", "size": SIZES["quicksort_gather"],
        # GatherInput. argv: n chunkSize chunksToSteal pollingInterval threads_request_policy stackSize partitioner [limitParallel]
        # custom: do_parallel(r) = r.size > limitParallel (base case: size <= 10000)
        "cmd": lambda p, c: "./bin/quicksort_dstack_gatherinput %d 1 1 0 0 1000 %d%s" % (SIZES["quicksort_gather"], p, opt(c, " %d")),
        "stdin": None,
        "codes": {"simple": 0, "custom": 1, "automatic": 2},
        "cutoffs": [(max(SIZES["quicksort_gather"] // 2, 10001), "custom_bad"),   # only the first level is parallel
                    (max(SIZES["quicksort_gather"] // 500, 10001), "custom_good"),  # default of the benchmark for n=10^8
                    (10001, "custom_many")],                                       # parallel down to the base case
    },
]

wanted = os.environ["TESTS"].split(",") if os.environ.get("TESTS") else TESTS_DEFAULT
TESTS = [t for t in ALL_TESTS if t["name"] in wanted]

# time and result printed by each benchmark
TIME_RE = [re.compile(r"compute time: ([0-9.eE+-]+)"),        # fib, NQueens, quicksort
           re.compile(r"^Time: ([0-9.eE+-]+)", re.M),          # topsorts
           re.compile(r"Wallclock time = ([0-9.eE+-]+)")]      # uts
RESULT_RE = [re.compile(r"fib\(\d+\): (\d+)"),
             re.compile(r"solutions: (\d+)"),
             re.compile(r"^Result: (\d+)", re.M),
             re.compile(r"Tree size = (\d+)"),
             re.compile(r"check results: \*?(SUCCESS|FAILURE)")]
HB_RE = re.compile(r"HB_STATS mode=run rank=\d+ thread=\d+ promotions=(\d+)")


def first_match(regexes, text):
    for r in regexes:
        m = r.search(text)
        if m:
            return m.group(1)
    return ""


def configurations(test):
    """(config, partitioner, cutoff, hb_period_us, hb_check_every) of one test"""
    confs = []
    if MODE != "tau":
        confs.append(("simple", "simple", None, None, None))
        if "custom" in test["codes"]:
            for cutoff, label in test["cutoffs"]:
                confs.append((label, "custom", cutoff, None, None))
    for period in HB_PERIODS:
        for check in HB_CHECK_EVERY:
            confs.append(("automatic", "automatic", None, period, check))
    return confs


if os.path.isdir(os.path.expanduser("~/skeleton")):
    os.chdir(os.path.expanduser("~/skeleton"))
elif not DRY_RUN:
    sys.exit("~/skeleton not found: run setup_all.sh first")

total = len(nodes_list) * REPS * sum(len(configurations(t)) for t in TESTS)
print("MODE=%s: %d runs (%d node counts x %d reps), %d threads per process"
      % (MODE, total, len(nodes_list), REPS, N_THREADS))

csv_name = "resultados_compare_%s.csv" % MODE
log_dir = "logs_compare_%s" % MODE
if not DRY_RUN:
    os.makedirs(log_dir, exist_ok=True)
    out = open(csv_name, "w", newline="")
else:
    out = open(os.devnull, "w")
csv_out = csv.writer(out)
csv_out.writerow(["nodos", "threads", "test", "size", "config", "partitioner", "cutoff", "hb_period_us",
                  "hb_check_every", "rep", "tiempo_wall_s", "tiempo_bench_s", "promociones", "resultado", "estado"])

problems = []
results_seen = {}   # (nodes, test) -> set of results

for n in nodes_list:
    if not DRY_RUN:
        lines = open("hosts").read().splitlines()[:n]
        open("hosts_tmp", "w").write("\n".join(lines) + "\n")

    for test in TESTS:
        for config, part, cutoff, period, check in configurations(test):
            command = test["cmd"](test["codes"][part], cutoff)
            env = dict(os.environ, OMP_NUM_THREADS=str(N_THREADS), DPR_HB_STATS="1")
            exports = "-x OMP_NUM_THREADS -x DPR_HB_STATS"
            env.pop("DPR_HB_PERIOD_US", None)
            env.pop("DPR_HB_CHECK_EVERY", None)
            if period is not None:
                env["DPR_HB_PERIOD_US"] = str(period)
                env["DPR_HB_CHECK_EVERY"] = str(check)
                # -x exports the variables to the processes of the other nodes too
                exports += " -x DPR_HB_PERIOD_US -x DPR_HB_CHECK_EVERY"
            cmd = ("mpirun --allow-run-as-root -np %d --hostfile hosts_tmp "
                   "--map-by ppr:1:node --bind-to none "
                   "--mca btl_tcp_if_exclude lo,docker0 %s %s" % (n, exports, command))

            for rep in range(REPS):
                tag = "%s_%s%s%s_n%d_r%d" % (test["name"], config, opt(cutoff, "_c%d"),
                                             "" if period is None else "_N%d_k%d" % (period, check), n, rep)
                if DRY_RUN:
                    print(("DPR_HB_PERIOD_US=%d " % period if period is not None else "") + cmd)
                    continue

                stdin = open(test["stdin"]) if test["stdin"] else subprocess.DEVNULL
                start = time.time()
                try:
                    proc = subprocess.run(cmd.split(), env=env, stdin=stdin, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, timeout=TIMEOUT)
                    text = proc.stdout.decode(errors="replace")
                    state = "ok" if proc.returncode == 0 else "error_%d" % proc.returncode
                except subprocess.TimeoutExpired as e:
                    text = (e.stdout or b"").decode(errors="replace")
                    state = "TIMEOUT"
                wall = round(time.time() - start, 3)
                open(os.path.join(log_dir, tag + ".txt"), "w").write(text)

                bench_time = first_match(TIME_RE, text)
                result = first_match(RESULT_RE, text)
                promotions = sum(int(x) for x in HB_RE.findall(text)) if part == "automatic" else ""

                csv_out.writerow([n, N_THREADS, test["name"], test["size"], config, part, opt(cutoff, "%d"),
                                  opt(period, "%d"), opt(check, "%d"), rep, wall, bench_time, promotions,
                                  result, state])
                out.flush()
                print(n, tag, "bench=%s wall=%s prom=%s res=%s %s" % (bench_time, wall, promotions, result, state))

                # checks (most useful in smoke mode)
                if state != "ok":
                    problems.append("%s: %s" % (tag, state))
                if result == "" or result == "FAILURE":
                    problems.append("%s: no valid result (%r)" % (tag, result))
                if part == "automatic" and N_THREADS * n > 1 and promotions == 0 and period is not None and period < 1000000:
                    problems.append("%s: automatic made 0 promotions" % tag)
                results_seen.setdefault((n, test["name"]), set()).add(result)

out.close()

if not DRY_RUN:
    for (n, name), res in sorted(results_seen.items()):
        if len(res) > 1:
            problems.append("%s with %d node(s): configurations disagree: %s" % (name, n, sorted(res)))
    print("\nCSV: %s   logs: %s/" % (csv_name, log_dir))
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    print("All runs ok, results agree" + (", automatic made promotions" if MODE != "tau" else ""))
