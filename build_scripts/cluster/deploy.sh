#!/bin/bash
# Corre en n1: compila en todos los nodos y arma ~/skeleton/hosts.
# uso: BRANCH=develop bash deploy.sh > ~/deploy.log 2>&1

set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
BRANCH=${BRANCH:-develop}
SETUP=$HERE/setup_all.sh
[ -f "$SETUP" ] || SETUP=$HERE/../setup_all.sh
[ -f "$SETUP" ] || { echo "ERROR: setup_all.sh not found next to deploy.sh"; exit 1; }
PATCH=""
for f in "$HERE/hb_fix.patch" "$(dirname "$SETUP")/hb_fix.patch"; do [ -f "$f" ] && { PATCH=$f; break; }; done
[ -f nodes.txt ] || { echo "ERROR: nodes.txt missing (copy nodes.txt.example and write the IPs)"; exit 1; }
[ -f id_cluster ] || { echo "ERROR: id_cluster (private key) missing"; exit 1; }
mapfile -t NODES < <(grep -v '^[[:space:]]*$' nodes.txt | grep -v '^#')
[ "${#NODES[@]}" -ge 1 ] || { echo "ERROR: nodes.txt has no IPs"; exit 1; }
SSH="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
echo "### nodes: ${NODES[*]}  branch: $BRANCH  patch: ${PATCH:-ninguno}"

echo "### 1/5 Cluster key and node fingerprints"
mkdir -p ~/.ssh
install -m 600 id_cluster ~/.ssh/id_ed25519
for ip in "${NODES[@]}"; do ssh-keyscan -H "$ip" >> ~/.ssh/known_hosts 2>/dev/null; done
for ip in "${NODES[@]}"; do $SSH root@$ip hostname || { echo "ERROR: n1 cannot enter $ip"; exit 1; }; done

echo "### 2/5 Send setup_all.sh and build on every node (10-15 min)"
for ip in "${NODES[@]}"; do
  (
    scp -q -o BatchMode=yes "$SETUP" root@$ip:~/setup_all.sh &&
    { [ -z "$PATCH" ] || scp -q -o BatchMode=yes "$PATCH" root@$ip:~/hb_fix.patch; } &&
    $SSH root@$ip "echo 'DPkg::Lock::Timeout \"600\";' > /etc/apt/apt.conf.d/99locktimeout;
                   mkdir -p /etc/needrestart/conf.d; echo \"\\\$nrconf{restart} = 'a';\" > /etc/needrestart/conf.d/99auto.conf;
                   cloud-init status --wait >/dev/null 2>&1 || true;
                   DEBIAN_FRONTEND=noninteractive BRANCH=$BRANCH bash ~/setup_all.sh > ~/setup.log 2>&1; echo EXIT=\$? >> ~/setup.log"
    echo "  $ip: setup finished"
  ) &
done
wait

echo "### 3/5 Check binaries and commit on every node"
fail=0; commits=""
for ip in "${NODES[@]}"; do
  last=$($SSH root@$ip "tail -1 ~/setup.log")
  bins=$($SSH root@$ip "cd ~/skeleton/bin 2>/dev/null && for b in fib_dstack NQueens_dstack uts_dstack topsorts_dstack quicksort_dstack_gatherinput; do [ -x \$b ] && echo -n \"\$b \"; done")
  c=$($SSH root@$ip "git -C ~/skeleton rev-parse --short HEAD 2>/dev/null; md5sum ~/skeleton/include/dparallel_recursion/HeartBeat.h | cut -c1-8; git -C ~/skeleton branch --show-current 2>/dev/null" | tr '\n' ' ')
  echo "  $ip  [$last]  commit/md5 HeartBeat.h/branch: $c  binaries: $bins"
  [ "$(echo $bins | wc -w)" -eq 5 ] || { echo "  ERROR: binaries missing on $ip (see ~/setup.log on that node)"; fail=1; }
  commits="$commits $(echo $c | cut -d' ' -f1-2 | tr ' ' '/')"
done
[ "$(echo $commits | tr ' ' '\n' | sort -u | wc -l)" -eq 1 ] || { echo "  ERROR: nodes are on different commits/HeartBeat.h:$commits"; fail=1; }
[ $fail -eq 0 ] || { echo "DEPLOY FAILED"; exit 1; }

echo "### 4/5 hosts file (n1 first)"
: > ~/skeleton/hosts
for ip in "${NODES[@]}"; do echo "$ip slots=8" >> ~/skeleton/hosts; done
cat ~/skeleton/hosts

echo "### 5/5 MPI on every node (must print every hostname)"
cd ~/skeleton
timeout 60 mpirun --allow-run-as-root -np ${#NODES[@]} --hostfile hosts --map-by ppr:1:node --bind-to none \
  --mca btl_tcp_if_exclude lo,docker0 hostname | sort || { echo "DEPLOY FAILED (mpirun)"; exit 1; }

echo "DEPLOY OK"
