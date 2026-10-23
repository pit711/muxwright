/* util.c – Zeitmessung */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "dvbmod.h"
#include <time.h>

double dvb_now(void)
{
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

double dvb_cpu_time(void)
{
    struct timespec ts; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

uint64_t dvb_cycles(void)
{
#if defined(__x86_64__) || defined(__i386__)
    unsigned lo, hi; __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi)); return ((uint64_t)hi << 32) | lo;
#else
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;   /* ns */
#endif
}
