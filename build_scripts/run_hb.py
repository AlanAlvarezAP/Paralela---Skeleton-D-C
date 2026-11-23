# Mide tau por benchmark y corre automatic en 1..4 nodos con ese N.
# uso: MODE=tau|nodes|all python3 build_scripts/run_hb.py [nodos] [reps]

import csv
import json
import math
import os
import re
import shlex
import signal
import statistics
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(line_buffering=True)
if hasattr(signal, "SIGPIPE"):
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

MODE = os.environ.get("MODE", "all")
if MODE not in ("tau", "nodes", "all"):
    sys.exit("MODE debe ser tau, nodes o all")

DRY_RUN = os.environ.get("DRY_RUN") == "1"
K = float(os.environ.get("K", "20"))
TAU_REPS = int(os.environ.get("TAU_REPS", "1"))
TAU_CHECK = int(os.environ.get("TAU_CHECK", "1"))
N_FALLBACK = int(os.environ.get("N_FALLBACK", "30"))
N_BIG = 1000000000
N_SMALL = 1
N_THREADS = int(os.environ.get("N_THREADS", "8"))
HB_CHECK_EVERY = int(os.environ.get("HB_CHECK_EVERY", "128"))
FIB_N = int(os.environ.get("FIB_N", "52"))
TOPSORTS_INPUT = os.environ.get("TOPSORTS_INPUT", "pm22")
BASELINES = [b for b in os.environ.get("BASELINES", "none").split(",") if b and b != "none"]
SKEL = os.path.expanduser(os.environ.get("SKEL", "~/skeleton"))
PACK = os.environ.get("PACK", "1") == "1"
OUT_TAG = os.environ.get("OUT_TAG", "")
SFX = "_" + OUT_TAG if OUT_TAG else ""
AUTOMATIC = os.environ.get("AUTOMATIC", "1") == "1"
STACK_KB = os.environ.get("STACK_KB", "")
if STACK_KB and not STACK_KB.isdigit():
    sys.exit("STACK_KB debe ser un entero (KB)")
LOCK = threading.Lock()

TESTS_WANTED = os.environ.get("TESTS", "fib,uts_T3XXL,uts_T2XL,topsorts").split(",")

T3XXL = "-t 0 -b 2000 -q 0.499995 -m 2 -r 316"
T2XL = "-t 1 -a 2 -d 26 -b 7 -r 220"
T3_TAU = "-t 0 -b 2000 -q 0.4999 -m 2 -r 316"
T2_TAU = "-t 1 -a 2 -d 20 -b 7 -r 220"
TOPS = "sequential/inputs/topsorts/%s.txt"


def opt(c, fmt):
    return "" if c is None else fmt % c


BENCH = {
    "fib": {
        "codes": {"simple": 0, "custom": 1, "automatic": 2},
        "cutoffs": {"custom_good": FIB_N - 17, "custom_bad": FIB_N - 5, "custom_default": 7},
        "tau": (lambda p, c: "./bin/fib_dstack 42 4 1 0 0 100 %d" % p, None, "fib(42)"),
        "big": (lambda p, c: "./bin/fib_dstack %d 4 1 0 0 100 %d%s" % (FIB_N, p, opt(c, " %d")), None, "fib(%d)" % FIB_N),
    },
    "uts_T3XXL": {
        "codes": {"simple": 1, "custom": 0, "automatic": 2},
        "cutoffs": {"custom_good": 5, "custom_bad": 1, "custom_many": 15},
        "tau": (lambda p, c: "./bin/uts_dstack %s -P %d" % (T3_TAU, p), None, "T3 q=0.4999"),
        "big": (lambda p, c: "./bin/uts_dstack %s -P %d%s" % (T3XXL, p, opt(c, " -U %d")), None, "T3XXL"),
    },
    "uts_T2XL": {
        "codes": {"simple": 1, "custom": 0, "automatic": 2},
        "cutoffs": {"custom_good": 5, "custom_bad": 1, "custom_many": 15},
        "tau": (lambda p, c: "./bin/uts_dstack %s -P %d" % (T2_TAU, p), None, "T2 d=20"),
        "big": (lambda p, c: "./bin/uts_dstack %s -P %d%s" % (T2XL, p, opt(c, " -U %d")), None, "T2XL"),
    },
    "topsorts": {
        "codes": {"simple": 0, "automatic": 2},
        "cutoffs": {},
        "tau": (lambda p, c: "./bin/topsorts_dstack 4 1 0 0 100 %d" % p, TOPS % "pm18", "pm18"),
        "big": (lambda p, c: "./bin/topsorts_dstack 4 1 0 0 100 %d" % p, TOPS % TOPSORTS_INPUT, TOPSORTS_INPUT),
    },
}
TESTS = [t for t in TESTS_WANTED if t in BENCH]
if not TESTS:
    sys.exit("TESTS no contiene ningun benchmark conocido: %s" % ",".join(BENCH))

TIME_RE = [re.compile(r"compute time: ([0-9.eE+-]+)"),
           re.compile(r"^Time: ([0-9.eE+-]+)", re.M),
           re.compile(r"Wallclock time = ([0-9.eE+-]+)")]
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


def all_hosts():
    if os.path.exists("hosts"):
        lines = [l.strip() for l in open("hosts").read().splitlines() if l.strip()]
    elif DRY_RUN:
        lines = ["n%d slots=%d" % (i, N_THREADS) for i in range(1, 5)]
    else:
        sys.exit("no existe hosts: corre primero deploy.sh")
    return lines


def write_hostfile(lines, tag):
    os.makedirs("hostfiles", exist_ok=True)
    path = os.path.join("hostfiles", tag)
    if not DRY_RUN:
        open(path, "w").write("\n".join(lines) + "\n")
    return path


def run_jobs(jobs):
    hosts = all_hosts()
    for j in jobs:
        if j["nodes"] > len(hosts):
            sys.exit("%s pide %d nodos y hosts tiene %d" % (j["tag"], j["nodes"], len(hosts)))
    free = list(range(len(hosts)))
    pending = list(jobs)
    state = {"running": 0}
    errors = []
    cond = threading.Condition()

    def worker(job, idx):
        names = [hosts[i].split()[0] for i in idx]
        try:
            job["fn"](write_hostfile([hosts[i] for i in idx], job["tag"]), names)
        except Exception as e:
            errors.append("%s: excepcion %r" % (job["tag"], e))
        finally:
            with cond:
                free.extend(idx)
                free.sort()
                state["running"] -= 1
                cond.notify_all()

    with cond:
        while pending or state["running"]:
            job = None
            if PACK or not state["running"]:
                job = next((j for j in pending if j["nodes"] <= len(free)), None)
            if job is None:
                cond.wait()
                continue
            pending.remove(job)
            idx, free[:] = free[:job["nodes"]], free[job["nodes"]:]
            state["running"] += 1
            if PACK:
                print("  >> inicia %s en %s (libres: %d)" % (job["tag"], ",".join(hosts[i].split()[0] for i in idx),
                                                            len(free)))
            threading.Thread(target=worker, args=(job, idx), daemon=True).start()
    return errors


def run(n, threads, command, stdin_path, period, check, log_dir, tag, timeout, hostfile="hosts_tmp"):
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), DPR_HB_STATS="1")
    exports = "-x OMP_NUM_THREADS -x DPR_HB_STATS"
    env.pop("DPR_HB_PERIOD_US", None)
    env.pop("DPR_HB_CHECK_EVERY", None)
    if period is not None:
        env["DPR_HB_PERIOD_US"] = str(period)
        env["DPR_HB_CHECK_EVERY"] = str(check)
        exports += " -x DPR_HB_PERIOD_US -x DPR_HB_CHECK_EVERY"
    if STACK_KB:
        command = "bash -c 'ulimit -s %s && exec %s'" % (STACK_KB, command)
    cmd = ("mpirun --allow-run-as-root -np %d --hostfile %s --map-by ppr:1:node --bind-to none "
           "--mca btl_tcp_if_exclude lo,docker0 %s %s" % (n, hostfile, exports, command))
    if DRY_RUN:
        with LOCK:
            print("   ", ("DPR_HB_PERIOD_US=%s DPR_HB_CHECK_EVERY=%s " % (period, check) if period else "") + cmd
                  + (" < " + stdin_path if stdin_path else ""))
        time.sleep(0.2 / n)
        return {"wall": 0, "bench": 1.0, "prom": 0, "res": "dry", "state": "ok"}
    stdin = open(stdin_path) if stdin_path else subprocess.DEVNULL
    start = time.time()
    try:
        proc = subprocess.run(shlex.split(cmd), env=env, stdin=stdin, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        text = proc.stdout.decode(errors="replace")
        state = "ok" if proc.returncode == 0 else "error_%d" % proc.returncode
    except subprocess.TimeoutExpired as e:
        text = (e.stdout or b"").decode(errors="replace")
        state = "TIMEOUT"
    wall = round(time.time() - start, 3)
    open(os.path.join(log_dir, tag + ".txt"), "w").write(text)
    bench = first_match(TIME_RE, text)
    return {"wall": wall, "bench": float(bench) if bench else None,
            "prom": sum(int(x) for x in HB_RE.findall(text)),
            "res": first_match(RESULT_RE, text), "state": state}


PROGRESS = {"done": 0}
JOB_ERRORS = []


def expected(total, phase):
    print("%d runs (%s)" % (total, phase))
    if not DRY_RUN:
        open(os.path.expanduser("~/EXPECTED_RUNS"), "w").write("%d runs (%s)\n" % (total, phase))
        open(os.path.expanduser("~/DONE_RUNS"), "w").write("0\n")


def progress():
    PROGRESS["done"] += 1
    if not DRY_RUN:
        open(os.path.expanduser("~/DONE_RUNS"), "w").write("%d\n" % PROGRESS["done"])


def phase_tau():
    print("\n######## FASE TAU: 1 nodo, 1 hilo, check_every=%d, %d reps, N_grande=%d us vs N=%d us, K=%g"
          % (TAU_CHECK, TAU_REPS, N_BIG, N_SMALL, K))
    timeout = int(os.environ.get("TIMEOUT", "900"))
    log_dir = "logs_hb_tau"
    os.makedirs(log_dir, exist_ok=True)
    print("%d corridas de tau%s" % (tau_runs(), ", un benchmark por nodo a la vez" if PACK else ""))
    out = open("resultados_hb_tau.csv", "w", newline="")
    w = csv.writer(out)
    w.writerow(["test", "entrada", "hb_period_us", "hb_check_every", "rep", "tiempo_bench_s", "tiempo_wall_s",
                "promociones", "resultado", "estado", "hosts", "inicio_s", "fin_s"])
    t0 = time.time()
    data = dict((name, {"times": {N_BIG: [], N_SMALL: []}, "proms": [], "results": set(), "problems": []})
                for name in TESTS)

    def make_job(name):
        cmd_f, stdin_path, label = BENCH[name]["tau"]
        command = cmd_f(BENCH[name]["codes"]["automatic"], None)
        d = data[name]

        def fn(hostfile, names):
            for rep in range(TAU_REPS):
                for period in (N_BIG, N_SMALL):
                    tag = "%s_tau_N%d_r%d" % (name, period, rep)
                    start = time.time() - t0
                    r = run(1, 1, command, stdin_path, period, TAU_CHECK, log_dir, tag, timeout, hostfile)
                    with LOCK:
                        progress()
                        w.writerow([name, label, period, TAU_CHECK, rep, r["bench"], r["wall"], r["prom"], r["res"],
                                    r["state"], " ".join(names), round(start, 1), round(time.time() - t0, 1)])
                        out.flush()
                        print("   %-10s rep %d N=%-10d bench=%-9s prom=%-8d res=%s %s  [%s]"
                              % (name, rep, period, r["bench"], r["prom"], r["res"], r["state"], " ".join(names)))
                        if r["state"] != "ok" or r["bench"] is None:
                            d["problems"].append("%s: %s" % (tag, r["state"]))
                            continue
                        d["times"][period].append(r["bench"])
                        d["results"].add(r["res"])
                        if period == N_SMALL:
                            d["proms"].append(r["prom"])
                        elif r["prom"] != 0:
                            d["problems"].append("%s: hubo %d promociones con N=1e9" % (tag, r["prom"]))
        return {"nodes": 1, "tag": "%s_tau" % name, "fn": fn}

    JOB_ERRORS.extend(run_jobs([make_job(name) for name in TESTS]))
    out.close()
    print("fase tau: %.1f s" % (time.time() - t0))

    summary = {}
    for name in TESTS:
        label = BENCH[name]["tau"][2]
        d = data[name]
        times, proms, results, problems = d["times"], d["proms"], d["results"], d["problems"]
        print("\n== %s (%s)" % (name, label))
        entry = {"entrada": label, "check_every": TAU_CHECK, "reps": TAU_REPS, "K": K, "problems": problems}
        if len(results) > 1:
            problems.append("resultados distintos entre corridas: %s" % sorted(results))
        if times[N_BIG] and times[N_SMALL] and proms:
            T, Tp, C = statistics.median(times[N_BIG]), statistics.median(times[N_SMALL]), statistics.median(proms)
            entry.update({"T_s": T, "Tprime_s": Tp, "C": C})
            if C > 0 and Tp > T:
                tau_us = (Tp - T) / C * 1e6
                N = max(1, int(math.ceil(K * tau_us)))
                entry.update({"tau_ns": round(tau_us * 1000, 2), "N_us": N, "fallback": False})
                spread = (max(times[N_BIG]) - min(times[N_BIG])) if len(times[N_BIG]) > 1 else 0.0
                entry["diff_s"] = round(Tp - T, 4)
                entry["spread_T_s"] = round(spread, 4)
                if spread > (Tp - T):
                    problems.append("T'-T (%.3f s) es menor que la dispersion de T (%.3f s): tau poco confiable"
                                    % (Tp - T, spread))
            else:
                entry.update({"tau_ns": None, "N_us": N_FALLBACK, "fallback": True})
                problems.append("T' <= T o C = 0 (T=%.3f T'=%.3f C=%d): se usa N_FALLBACK=%d"
                                % (T, Tp, C, N_FALLBACK))
        else:
            entry.update({"tau_ns": None, "N_us": N_FALLBACK, "fallback": True})
            problems.append("sin corridas validas: se usa N_FALLBACK=%d" % N_FALLBACK)
        summary[name] = entry
        print("   -> T=%s T'=%s C=%s  tau=%s ns  N=%d us%s"
              % (entry.get("T_s"), entry.get("Tprime_s"), entry.get("C"), entry["tau_ns"], entry["N_us"],
                 "  (RESPALDO)" if entry["fallback"] else ""))
        for p in problems:
            print("   AVISO:", p)
    if not DRY_RUN:
        json.dump(summary, open("tau_N.json", "w"), indent=2)
    print("\n%-11s %-12s %9s %9s %10s %9s %6s" % ("test", "entrada", "T (s)", "T' (s)", "C", "tau (ns)", "N us"))
    for name, e in summary.items():
        print("%-11s %-12s %9s %9s %10s %9s %6d%s" % (name, e["entrada"], fmt(e.get("T_s")), fmt(e.get("Tprime_s")),
              e.get("C"), e["tau_ns"], e["N_us"], " (respaldo)" if e["fallback"] else ""))
    print("tau_N.json, resultados_hb_tau.csv, %s/" % log_dir)
    return summary


def fmt(x):
    return "-" if x is None else "%.3f" % x


def periods_for_nodes(summary):
    periods = {}
    if not AUTOMATIC:
        return periods
    for name in TESTS:
        forced = os.environ.get("N_" + name)
        if forced:
            periods[name] = int(forced)
        elif summary and name in summary:
            periods[name] = int(summary[name]["N_us"])
        elif DRY_RUN:
            periods[name] = N_FALLBACK
        else:
            sys.exit("no hay N para %s: corre primero MODE=tau (tau_N.json) o pasa N_%s=..." % (name, name))
    return periods


def build_plan(periods):
    plan = []
    for name in TESTS:
        b = BENCH[name]
        for base in BASELINES:
            if base == "simple":
                plan.append((name, "simple", "simple", None, None))
            elif base in b["cutoffs"] and "custom" in b["codes"]:
                plan.append((name, base, "custom", b["cutoffs"][base], None))
        if AUTOMATIC:
            plan.append((name, "automatic", "automatic", None, periods.get(name)))
    return plan


def tau_runs():
    return len(TESTS) * 2 * TAU_REPS


def nodes_runs(nodes_list, reps):
    return len(build_plan({})) * len(nodes_list) * reps


def phase_nodes(summary, nodes_list, reps):
    periods = periods_for_nodes(summary)
    timeout = int(os.environ.get("TIMEOUT", "2400"))
    log_dir = "logs_hb_nodes" + SFX
    os.makedirs(log_dir, exist_ok=True)

    plan = build_plan(periods)

    total = len(plan) * len(nodes_list) * reps
    print("\n######## FASE NODES: nodos=%s, %d hilos/nodo, %d reps, check_every=%d -> %d corridas"
          % (",".join(map(str, nodes_list)), N_THREADS, reps, HB_CHECK_EVERY, total))
    print("N por benchmark: " + (", ".join("%s=%d us" % kv for kv in periods.items()) or "- (sin automatic)")
          + ("   pila por hilo: %s KB" % STACK_KB if STACK_KB else ""))

    out = open("resultados_hb_nodes%s.csv" % SFX, "w", newline="")
    w = csv.writer(out)
    w.writerow(["nodos", "threads", "test", "entrada", "config", "partitioner", "cutoff", "hb_period_us",
                "hb_check_every", "rep", "tiempo_wall_s", "tiempo_bench_s", "promociones", "resultado", "estado",
                "hosts", "inicio_s", "fin_s", "stack_kb"])
    times, results, problems = {}, {}, []
    counter = {"done": 0}
    t0 = time.time()

    def make_job(rep, n, name, config, part, cutoff, period):
        cmd_f, stdin_path, label = BENCH[name]["big"]
        command = cmd_f(BENCH[name]["codes"][part], cutoff)
        tag = "%s_%s%s%s_n%d_r%d" % (name, config, opt(cutoff, "_c%d"), opt(period, "_N%d"), n, rep)

        def fn(hostfile, names):
            start = time.time() - t0
            r = run(n, N_THREADS, command, stdin_path, period, HB_CHECK_EVERY, log_dir, tag, timeout, hostfile)
            with LOCK:
                counter["done"] += 1
                progress()
                w.writerow([n, N_THREADS, name, label, config, part, opt(cutoff, "%d"), opt(period, "%d"),
                            HB_CHECK_EVERY if period else "", rep, r["wall"], r["bench"],
                            r["prom"] if part == "automatic" else "", r["res"], r["state"],
                            " ".join(names), round(start, 1), round(time.time() - t0, 1), STACK_KB])
                out.flush()
                print("[%d/%d] %s bench=%s wall=%s prom=%s res=%s %s  [%s]"
                      % (counter["done"], total, tag, r["bench"], r["wall"],
                         r["prom"] if part == "automatic" else "-", r["res"], r["state"], " ".join(names)))
                if r["state"] != "ok" or r["bench"] is None:
                    problems.append("%s: %s" % (tag, r["state"]))
                    return
                if r["res"] in ("", "FAILURE"):
                    problems.append("%s: sin resultado valido (%r)" % (tag, r["res"]))
                times.setdefault((name, config, n), []).append(r["bench"])
                results.setdefault(name, set()).add(r["res"])
                if part == "automatic" and N_THREADS * n > 1 and r["prom"] == 0:
                    problems.append("%s: automatic hizo 0 promociones" % tag)
        return {"nodes": n, "tag": tag, "fn": fn}

    jobs = []
    for rep in range(reps):
        for n in nodes_list:
            for name, config, part, cutoff, period in plan:
                jobs.append(make_job(rep, n, name, config, part, cutoff, period))
    if PACK:
        jobs.sort(key=lambda j: (-j["nodes"],))
    JOB_ERRORS.extend(run_jobs(jobs))
    out.close()
    print("fase nodes: %.1f s" % (time.time() - t0))

    for name, res in results.items():
        if len(res) > 1:
            problems.append("%s: las configuraciones dan resultados distintos: %s" % (name, sorted(res)))

    lines = []
    lines.append("%-11s %-18s" % ("test", "config") + "".join("%11s" % ("%d nodo%s" % (n, "s" if n > 1 else ""))
                                                       for n in nodes_list))
    configs = []
    for name, config, _, _, _ in plan:
        if (name, config) not in configs:
            configs.append((name, config))
    for name, config in configs:
        row = "%-11s %-18s" % (name, config)
        for n in nodes_list:
            v = times.get((name, config, n))
            row += "%11s" % ("%.3f" % statistics.median(v) if v else "-")
        lines.append(row)
    lines.append("")
    lines.append("speedup respecto a 1 nodo (mediana) y automatic / linea base (< 1 = automatic gana)")
    for name, config in configs:
        b1 = times.get((name, config, nodes_list[0]))
        row = "%-11s %-18s" % (name, config)
        for n in nodes_list:
            v = times.get((name, config, n))
            row += "%11s" % ("x%.2f" % (statistics.median(b1) / statistics.median(v)) if v and b1 else "-")
        lines.append(row)
    for name in TESTS:
        for base in [c for (t, c) in configs if t == name and c != "automatic"]:
            row = "%-11s %-18s" % (name, "auto/" + base)
            for n in nodes_list:
                a, b = times.get((name, "automatic", n)), times.get((name, base, n))
                row += "%11s" % ("%.3f" % (statistics.median(a) / statistics.median(b)) if a and b else "-")
            lines.append(row)
    table = "\n".join(lines)
    print("\n" + table)
    if not DRY_RUN:
        open("resumen_hb_nodes%s.txt" % SFX, "w").write(
            "N por benchmark: " + json.dumps(periods) + "\n\n" + table + "\n")
    print("\nresultados_hb_nodes%s.csv, resumen_hb_nodes%s.txt, %s/" % (SFX, SFX, log_dir))
    return problems


if os.path.isdir(SKEL):
    os.chdir(SKEL)
elif not DRY_RUN:
    sys.exit("%s no existe: corre primero setup_all.sh" % SKEL)

if len(sys.argv) > 1:
    nodes_list = [int(x) for x in sys.argv[1].split(",")]
elif os.path.exists("hosts"):
    nodes_list = list(range(1, len([l for l in open("hosts") if l.strip()]) + 1))
else:
    nodes_list = [1, 2, 3, 4]
reps = int(sys.argv[2]) if len(sys.argv) > 2 else 1

n_tau = tau_runs() if MODE in ("tau", "all") else 0
n_nodes = nodes_runs(nodes_list, reps) if MODE in ("nodes", "all") else 0
total = n_tau + n_nodes
expected(total, "MODE=%s: tau %d + nodes %d" % (MODE, n_tau, n_nodes))

all_problems = []
summary = None
if MODE in ("tau", "all"):
    summary = phase_tau()
    for name, e in summary.items():
        all_problems += ["tau %s: %s" % (name, p) for p in e["problems"]]
elif os.path.exists("tau_N.json"):
    summary = json.load(open("tau_N.json"))
    print("usando tau_N.json: " + ", ".join("%s=%s us" % (k, v["N_us"]) for k, v in summary.items()))

if MODE in ("nodes", "all"):
    all_problems += phase_nodes(summary, nodes_list, reps)

all_problems += JOB_ERRORS
if DRY_RUN:
    print("\nDRY_RUN: solo se imprimieron los comandos (%d corridas)" % total)
    sys.exit(0)
if all_problems:
    print("\nPROBLEMAS (%d):" % len(all_problems))
    for p in all_problems:
        print("  - " + p)
    sys.exit(1)
print("\nTodo ok")
