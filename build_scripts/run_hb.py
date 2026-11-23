import argparse
import csv
import math
import os
import re
import subprocess

p = argparse.ArgumentParser()
p.add_argument("nodes", nargs="?", default="1,2,3,4")
p.add_argument("--tau", action="store_true", help="measure tau and N first (1 node, 1 thread)")
p.add_argument("--simple", action="store_true")
p.add_argument("--custom", action="store_true")
p.add_argument("--reps", type=int, default=1)
args = p.parse_args()

os.chdir(os.path.expanduser("~/skeleton"))
os.makedirs("logs_hb", exist_ok=True)

T3XXL = "-t 0 -b 2000 -q 0.499995 -m 2 -r 316"
T2XL = "-t 1 -a 2 -d 26 -b 7 -r 220"
TOPS = "sequential/inputs/topsorts/%s.txt"

# name: (command, tau command, input, tau input, simple/custom/automatic codes, custom cutoff)
tests = {
    "fib": ("./bin/fib_dstack 52 4 1 0 0 100 {p}{c}", "./bin/fib_dstack 42 4 1 0 0 100 2",
            None, None, (0, 1, 2), " 7"),
    "uts_T3XXL": ("./bin/uts_dstack " + T3XXL + " -P {p}{c}", "./bin/uts_dstack -t 0 -b 2000 -q 0.4999 -m 2 -r 316 -P 2",
                  None, None, (1, 0, 2), " -U 5"),
    "uts_T2XL": ("./bin/uts_dstack " + T2XL + " -P {p}{c}", "./bin/uts_dstack -t 1 -a 2 -d 20 -b 7 -r 220 -P 2",
                 None, None, (1, 0, 2), " -U 5"),
    "topsorts": ("./bin/topsorts_dstack 4 1 0 0 100 {p}{c}", "./bin/topsorts_dstack 4 1 0 0 100 2",
                 TOPS % "pm22", TOPS % "pm18", (0, None, 2), ""),
}


def run(n, threads, command, inp, N, check, tag):
    open("hosts_tmp", "w").write("\n".join(open("hosts").read().splitlines()[:n]) + "\n")
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), DPR_HB_STATS="1",
               DPR_HB_PERIOD_US=str(N), DPR_HB_CHECK_EVERY=str(check))
    cmd = ("mpirun --allow-run-as-root -np %d --hostfile hosts_tmp --map-by ppr:1:node --bind-to none "
           "--mca btl_tcp_if_exclude lo,docker0 -x OMP_NUM_THREADS -x DPR_HB_STATS -x DPR_HB_PERIOD_US "
           "-x DPR_HB_CHECK_EVERY %s" % (n, command))
    out = subprocess.run(cmd.split(), env=env, stdin=open(inp) if inp else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode(errors="replace")
    open("logs_hb/%s.txt" % tag, "w").write(out)
    t = re.search(r"compute time: ([\d.]+)|Time: ([\d.]+)|Wallclock time = ([\d.]+)", out)
    time = float(next(x for x in t.groups() if x)) if t else None
    return time, sum(int(x) for x in re.findall(r"promotions=(\d+)", out))


# tau = (T' - T) / C with N = 1e9 (no promotions) and N = 1, then N = ceil(20 * tau)
N = dict.fromkeys(tests, 30)
if args.tau:
    with open("tau_N.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["test", "T", "T'", "C", "tau_ns", "N_us"])
        for name, (_, cmd, _, inp, _, _) in tests.items():
            T, _ = run(1, 1, cmd, inp, 10 ** 9, 1, name + "_tau_big")
            Tp, C = run(1, 1, cmd, inp, 1, 1, name + "_tau_small")
            tau = (Tp - T) / C if T and Tp and C and Tp > T else None
            if tau:
                N[name] = max(1, math.ceil(20 * tau * 1e6))
            w.writerow([name, T, Tp, C, tau and round(tau * 1e9, 2), N[name]])
            print(name, "T=%s T'=%s C=%s -> N=%d us" % (T, Tp, C, N[name]))
elif os.path.exists("tau_N.csv"):
    N.update((r["test"], int(r["N_us"])) for r in csv.DictReader(open("tau_N.csv")))

nodes = [int(x) for x in args.nodes.split(",")]
times = {}
with open("results_hb.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["nodes", "test", "config", "N_us", "rep", "time_s", "promotions"])
    for rep in range(args.reps):
        for n in nodes:
            for name, (cmd, _, inp, _, (cs, cc, ca), cut) in tests.items():
                configs = [("automatic", ca, "")]
                if args.simple:
                    configs.append(("simple", cs, ""))
                if args.custom and cc is not None:
                    configs.append(("custom", cc, cut))
                for config, code, c in configs:
                    t, prom = run(n, 8, cmd.format(p=code, c=c), inp, N[name], 128,
                                  "%s_%s_n%d_r%d" % (name, config, n, rep))
                    times.setdefault((name, config), {}).setdefault(n, []).append(t)
                    w.writerow([n, name, config, N[name] if config == "automatic" else "", rep, t, prom])
                    f.flush()
                    print(n, name, config, t, "s")

print("\n%-22s" % "test" + "".join("%10s" % ("%d node(s)" % n) for n in nodes))
for (name, config), by_n in times.items():
    print("%-22s" % (name + " " + config) + "".join(
        "%10.2f" % (sum(v) / len(v)) if all(v) else "%10s" % "-" for v in (by_n[n] for n in nodes)))
