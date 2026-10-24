/* dvbt2_fft.h – inverse int16-FFT (Radix-8 gemischt, N = 1K … 32K) für dvbt2_mod, siehe dvbt2_fft.c */
#ifndef DVBT2_FFT_H
#define DVBT2_FFT_H
#include <stdint.h>
typedef struct t2fft t2fft_t;
t2fft_t *t2fft_create(int n);                 /* n = 2^10 … 2^15, sonst NULL */
void     t2fft_free(t2fft_t *f);
int      t2fft_shift_bits(const t2fft_t *f);  /* Gesamtskalierung 2^-S, S = floor(log2 n / 2) */
/* Eingang interleaved I/Q (n Bins, Bin 0 = DC), Ausgang interleaved I/Q mit zyklischem Präfix (guard + n Samples) */
void     t2fft_ifft(t2fft_t *f, const int16_t *in_iq, int16_t *out_iq, int guard);
const char *t2fft_backend(void);
#endif
