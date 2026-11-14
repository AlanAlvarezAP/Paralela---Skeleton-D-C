import sys
import csv
import time
import subprocess
import os

N_THREADS = 8
os.chdir(os.path.expanduser("~/skeleton"))


text_input = sys.argv[1] if len(sys.argv) > 1 else "1"
nodes_list = [int(x) for x in text_input.split(",")]


T3XXL = "-t 0 -b 2000 -q 0.499995 -m 2 -r 316"
T2XL = "-t 1 -a 2 -d 26 -b 7 -r 220"

tests = [
    ("uts_T3XXL", "./bin/uts_dstack " + T3XXL, None),
    ("uts_T2XL", "./bin/uts_dstack " + T2XL, None),
    ("NQueens_17", "./bin/NQueens_dstack 17", None),
    ("fib_57", "./bin/fib_dstack 57", None),
    ("topsorts_cat42", "./bin/topsorts_dstack", "sequential/inputs/topsorts/cat42.txt"),
]


# Measure
tiempos = {}
with open("resultados.csv", "w", newline="") as f:
    csv_out = csv.writer(f)
    csv_out.writerow(["nodos", "test", "tiempo_s"])

    for n in nodes_list:
        lineas = open("hosts").read().splitlines()[:n]
        open("hosts_tmp", "w").write("\n".join(lineas) + "\n")

        for nombre, comando, entrada in tests:
            cmd = ("mpirun --allow-run-as-root -np %d --hostfile hosts_tmp "
                   "--map-by ppr:1:node --bind-to none "
                   "--mca btl_tcp_if_exclude lo,docker0 -x OMP_NUM_THREADS %s"
                   % (n, comando))
            
            env = dict(os.environ, OMP_NUM_THREADS=str(N_THREADS))
            stdin = open(entrada) if entrada else subprocess.DEVNULL
            
            log = open("log_%s_n%d.txt" % (nombre, n), "w")

            inicio = time.time()
            subprocess.run(cmd.split(), env = env, stdin = stdin,
                           stdout = log, stderr = subprocess.STDOUT)
            tiempo = round(time.time() - inicio, 2)

            tiempos[(nombre, n)] = tiempo
            csv_out.writerow([n, nombre, tiempo])
            f.flush()
            print(n, nombre, tiempo, "s")

# Final table
print("\n%-16s" % "test" + "".join("%10s" % ("%d nodo(s)" % n) for n in nodes_list))
for nombre, _, _ in tests:
    print("%-16s" % nombre + "".join("%10.2f" % tiempos[(nombre, n)] for n in nodes_list))