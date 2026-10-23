#!/bin/sh
cd /tmp
echo "## prozesse (top):"; top -b -n1 2>/dev/null | head -12 || ps
echo "## 1 thread, profile"; ./mod_unfused --bench 1088 --mode 8k --const 64qam --rate 7/8 --threads 1 --profile --quiet 2>&1 | tail -1
echo "## 2 threads ohne affinity"; ./mod_unfused --bench 1088 --mode 8k --const 64qam --rate 7/8 --profile --quiet 2>&1 | tail -1
echo "## 2 threads affinity + SCHED_FIFO"; ./mod_unfused --bench 1088 --mode 8k --const 64qam --rate 7/8 --affinity --rt --profile --quiet 2>&1 | tail -1
echo "## 2 threads affinity, 8k 16qam 3/4"; ./mod_unfused --bench 1088 --mode 8k --const 16qam --rate 3/4 --affinity --profile --quiet 2>&1 | tail -1
echo "## 2 threads affinity, 8k qpsk 1/2"; ./mod_unfused --bench 1088 --mode 8k --const qpsk --rate 1/2 --affinity --profile --quiet 2>&1 | tail -1
echo "## 2 threads affinity, 2k 64qam 7/8"; ./mod_unfused --bench 4352 --mode 2k --const 64qam --rate 7/8 --affinity --profile --quiet 2>&1 | tail -1
echo "## loadavg: $(cat /proc/loadavg)"
