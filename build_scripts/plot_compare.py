"""
Plots and summary tables from the CSV written by run_compare.py.

Usage (anywhere with Python 3 + matplotlib, e.g. locally after copying the CSV from the cluster):
    python3 build_scripts/plot_compare.py resultados_compare_full.csv [output_dir]
    python3 build_scripts/plot_compare.py resultados_compare_tau.csv  [output_dir]

The time used is the median of the repetitions of tiempo_bench_s (the time printed by the
benchmark); if a benchmark printed nothing, tiempo_wall_s is used. Runs whose estado is not
"ok" are left out (and listed).

Output (output_dir, default "graficas_<csv name>"):
    comparacion_<test>.png    simple vs each custom cutoff vs automatic (best N and N=30), per node count
    barrido_N_<test>.png      time of automatic vs N, with simple and the best custom as reference
    promociones_<test>.png    promotions vs N
    escalabilidad.png         time vs nodes: simple, best and worst custom, automatic with best N
    resumen.csv               median time, speedup vs simple, promotions of every configuration
    tau.txt                   (tau CSV only) cost of one promotion and the suggested N
"""

import csv
import os
import statistics
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# validated categorical palette (fixed order, one hue per partitioner family)
COLOR = {"simple": "#2a78d6", "custom": "#eb6834", "automatic": "#1baf7a", "custom_worst": "#eda100"}
INK, INK2, GRID = "#1f1f1e", "#5f5e58", "#e4e3df"
DEFAULT_N = 30

plt.rcParams.update({
    "font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "axes.axisbelow": True,
    "legend.frameon": False, "figure.dpi": 110, "savefig.bbox": "tight",
})

if len(sys.argv) < 2:
    sys.exit(__doc__)
csv_path = sys.argv[1]
out_dir = sys.argv[2] if len(sys.argv) > 2 else "graficas_" + os.path.splitext(os.path.basename(csv_path))[0]
os.makedirs(out_dir, exist_ok=True)

rows = list(csv.DictReader(open(csv_path)))
bad = [r for r in rows if r["estado"] != "ok"]
rows = [r for r in rows if r["estado"] == "ok"]
if bad:
    print("Left out %d runs that did not finish ok:" % len(bad))
    for r in bad:
        print("  ", r["nodos"], r["test"], r["config"], r["cutoff"], r["hb_period_us"], r["estado"])


def run_time(r):
    t = r["tiempo_bench_s"] or r["tiempo_wall_s"]
    return float(t) if t else None


# group the repetitions: key = (test, nodes, partitioner, config, cutoff, N, check_every)
groups = defaultdict(list)
proms = defaultdict(list)
for r in rows:
    t = run_time(r)
    if t is None:
        continue
    key = (r["test"], int(r["nodos"]), r["partitioner"], r["config"], r["cutoff"], r["hb_period_us"], r["hb_check_every"])
    groups[key].append(t)
    if r["promociones"] != "":
        proms[key].append(int(r["promociones"]))

if not groups:
    sys.exit("No valid runs in %s" % csv_path)


def med(key):
    return statistics.median(groups[key])


tests = sorted({k[0] for k in groups})
nodes_all = sorted({k[1] for k in groups})


def keys_of(test, nodes, part=None):
    return [k for k in groups if k[0] == test and k[1] == nodes and (part is None or k[2] == part)]


def best(test, nodes, part, worst=False):
    ks = keys_of(test, nodes, part)
    if not ks:
        return None
    return (max if worst else min)(ks, key=med)


def label_of(k):
    if k[2] == "simple":
        return "simple"
    if k[2] == "custom":
        return "custom\n%s" % k[4]
    return "auto\nN=%s" % k[5]


def save(fig, name):
    path = os.path.join(out_dir, name)
    fig.savefig(path)
    plt.close(fig)
    print("wrote", path)


# ---------------- tau experiment ----------------
if all(k[2] == "automatic" for k in groups) and len({k[5] for k in groups}) == 2:
    lines = []
    for test in tests:
        for n in nodes_all:
            ks = sorted(keys_of(test, n, "automatic"), key=lambda k: int(k[5]))
            if len(ks) != 2:
                continue
            small, big = ks
            t_small, t_big = med(small), med(big)
            c = statistics.median(proms[small]) if proms[small] else 0
            if c > 0:
                tau_us = (t_small - t_big) / c * 1e6
                lines.append("%s, %d node(s): T(N=%s)=%.4f s  T(N=%s)=%.4f s  promotions(N=%s)=%d" %
                             (test, n, big[5], t_big, small[5], t_small, small[5], c))
                lines.append("  tau = %.3f us per promotion  ->  suggested N = 20*tau = %.1f us" % (tau_us, 20 * tau_us))
            else:
                lines.append("%s, %d node(s): 0 promotions with N=%s, tau cannot be computed" % (test, n, small[5]))
    text = "\n".join(lines) + "\n"
    open(os.path.join(out_dir, "tau.txt"), "w").write(text)
    print(text)
    sys.exit(0)

# ---------------- summary table ----------------
with open(os.path.join(out_dir, "resumen.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["test", "nodos", "partitioner", "config", "cutoff", "hb_period_us", "hb_check_every",
                "reps", "tiempo_mediana_s", "tiempo_min_s", "tiempo_max_s", "speedup_vs_simple", "promociones_mediana"])
    for k in sorted(groups, key=lambda k: (k[0], k[1], k[2], k[4].zfill(12), k[5].zfill(12))):
        s = best(k[0], k[1], "simple")
        speedup = med(s) / med(k) if s else ""
        w.writerow([k[0], k[1], k[2], k[3], k[4], k[5], k[6], len(groups[k]), "%.4f" % med(k),
                    "%.4f" % min(groups[k]), "%.4f" % max(groups[k]),
                    "%.3f" % speedup if speedup != "" else "",
                    statistics.median(proms[k]) if proms[k] else ""])
print("wrote", os.path.join(out_dir, "resumen.csv"))

# ---------------- 1. comparison bars ----------------
for test in tests:
    ns = [n for n in nodes_all if keys_of(test, n)]
    nbars = max(len(keys_of(test, n, "simple")) + len(keys_of(test, n, "custom")) + 2 for n in ns)
    fig, axes = plt.subplots(1, len(ns), figsize=(max(5.5, (0.75 * nbars + 1.2) * len(ns)), 4.4), squeeze=False, sharey=False)
    for ax, n in zip(axes[0], ns):
        bars = []
        bars += keys_of(test, n, "simple")
        bars += sorted(keys_of(test, n, "custom"), key=lambda k: float(k[4]))
        auto = keys_of(test, n, "automatic")
        if auto:
            b = min(auto, key=med)
            bars.append(b)
            dflt = [k for k in auto if k[5] == str(DEFAULT_N) and k != b]
            bars += dflt
        xs = range(len(bars))
        vals = [med(k) for k in bars]
        lo = [med(k) - min(groups[k]) for k in bars]
        hi = [max(groups[k]) - med(k) for k in bars]
        ax.bar(xs, vals, width=0.7, color=[COLOR[k[2]] for k in bars], edgecolor="white", linewidth=2,
               yerr=[lo, hi], error_kw={"ecolor": INK2, "elinewidth": 1, "capsize": 3})
        for x, v in zip(xs, vals):
            ax.annotate("%.2f" % v, (x, v), xytext=(0, 4), textcoords="offset points",
                        ha="center", va="bottom", fontsize=8, color=INK)
        labels = [label_of(k) for k in bars]
        if auto:
            labels[len(bars) - 1 - len(dflt)] += "\n(mejor)"
        ax.set_xticks(list(xs))
        ax.set_xticklabels(labels, fontsize=7.5)
        ax.set_xlabel("custom: valor del cutoff · auto: N en µs", fontsize=8, color=INK2)
        ax.set_title("%d nodo(s)" % n, color=INK, fontsize=10)
        ax.set_ylabel("tiempo (s)")
        ax.grid(axis="x", visible=False)
    fig.suptitle("%s: simple vs custom vs automatic (mediana, barras = min-max)" % test, color=INK)
    save(fig, "comparacion_%s.png" % test)

# ---------------- 2. sweep of N and 3. promotions ----------------
for test in tests:
    ns = [n for n in nodes_all if len(keys_of(test, n, "automatic")) > 1]
    if not ns:
        continue
    checks = sorted({k[6] for n in ns for k in keys_of(test, n, "automatic")}, key=int)

    fig, axes = plt.subplots(1, len(ns), figsize=(max(5.5, 4.2 * len(ns)), 4), squeeze=False)
    for ax, n in zip(axes[0], ns):
        for i, ce in enumerate(checks):
            ks = sorted([k for k in keys_of(test, n, "automatic") if k[6] == ce], key=lambda k: int(k[5]))
            x = [int(k[5]) for k in ks]
            y = [med(k) for k in ks]
            name = "automatic" + ("" if len(checks) == 1 else " (check_every=%s)" % ce)
            ax.plot(x, y, marker="o", markersize=6, linewidth=2, color=COLOR["automatic"],
                    alpha=1.0 if i == 0 else 0.55, linestyle="-" if i == 0 else "--", label=name)
        for part, ref_name in (("simple", "simple"), ("custom", "mejor custom")):
            b = best(test, n, part)
            if b:
                ax.axhline(med(b), color=COLOR[part], linewidth=1.5, linestyle=":",
                           label="%s (%.2f s)" % (ref_name if part == "simple" else "%s, cutoff=%s" % (ref_name, b[4]), med(b)))
        ax.set_xscale("log")
        ax.set_xlabel("N, periodo del latido (µs, escala log)")
        ax.set_ylabel("tiempo (s)")
        ax.set_title("%d nodo(s)" % n, color=INK, fontsize=10)
        ax.legend(fontsize=8)
    fig.suptitle("%s: tiempo de automatic según N" % test, color=INK)
    save(fig, "barrido_N_%s.png" % test)

    fig, ax = plt.subplots(figsize=(6, 4))
    shades = ["#1baf7a", "#2a78d6", "#eb6834", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
    for i, n in enumerate(ns):
        ks = sorted([k for k in keys_of(test, n, "automatic") if k[6] == checks[0] and proms[k]], key=lambda k: int(k[5]))
        if ks:
            ax.plot([int(k[5]) for k in ks], [max(statistics.median(proms[k]), 1) for k in ks], marker="o",
                    markersize=6, linewidth=2, color=shades[i % len(shades)], label="%d nodo(s)" % n)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("N, periodo del latido (µs, escala log)")
    ax.set_ylabel("promociones (total de todos los hilos, log)")
    ax.set_title("%s: promociones según N" % test, color=INK)
    if len(ns) > 1:
        ax.legend(fontsize=8)
    save(fig, "promociones_%s.png" % test)

# ---------------- 4. scalability ----------------
if len(nodes_all) > 1:
    fig, axes = plt.subplots(1, len(tests), figsize=(4.2 * len(tests), 4), squeeze=False)
    for ax, test in zip(axes[0], tests):
        series = (("simple", "simple", False, COLOR["simple"]),
                  ("custom", "mejor custom", False, COLOR["custom"]),
                  ("custom", "peor custom", True, COLOR["custom_worst"]),
                  ("automatic", "automatic (mejor N)", False, COLOR["automatic"]))
        for part, name, worst, color in series:
            pts = [(n, best(test, n, part, worst)) for n in nodes_all]
            pts = [(n, k) for n, k in pts if k]
            if pts:
                ax.plot([n for n, _ in pts], [med(k) for _, k in pts], marker="o", markersize=6,
                        linewidth=2, color=color, label=name)
        ax.set_xticks(nodes_all)
        ax.set_xlabel("nodos")
        ax.set_ylabel("tiempo (s)")
        ax.set_title(test, color=INK, fontsize=10)
        ax.legend(fontsize=7.5)
    fig.suptitle("Escalabilidad (mediana de las repeticiones)", color=INK)
    save(fig, "escalabilidad.png")
