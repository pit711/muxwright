// doomgeneric fuer HbbTV-Browser (Muxwright): reines JavaScript (asm.js, kein WebAssembly/WebGL),
// Bild per Canvas 2D, Tasten aus der Fernbedienung ueber dg_key() (aus JavaScript aufgerufen), kein Ton.
// Lizenz: GPL-2.0 oder spaeter (wie doomgeneric / Chocolate Doom).

#include "doomkeys.h"
#include "doomgeneric.h"

#include <stdint.h>
#include <emscripten.h>

#define KEYQUEUE_SIZE 64

static unsigned short s_KeyQueue[KEYQUEUE_SIZE];
static unsigned int s_KeyQueueWriteIndex = 0;
static unsigned int s_KeyQueueReadIndex = 0;

// Von JavaScript: pressed 1/0, key = Doom-Tastencode (doomkeys.h bzw. ASCII)
EMSCRIPTEN_KEEPALIVE void dg_key(int pressed, int key)
{
  s_KeyQueue[s_KeyQueueWriteIndex] = (unsigned short)(((pressed ? 1 : 0) << 8) | (key & 0xFF));
  s_KeyQueueWriteIndex = (s_KeyQueueWriteIndex + 1) % KEYQUEUE_SIZE;
}

// XRGB8888 (DG_ScreenBuffer) -> RGBA im Canvas; Kontext und ImageData werden beim ersten Bild angelegt
EM_JS(void, js_draw, (uint32_t * buf, int w, int h), {
  var c = Module.dgCanvas || (Module.dgCanvas = document.getElementById('doom'));
  if (!Module.dgCtx) {
    Module.dgCtx = c.getContext('2d');
    Module.dgImg = Module.dgCtx.createImageData(w, h);
    Module.dgDst = new Uint32Array(Module.dgImg.data.buffer);
  }
  var dst = Module.dgDst, n = w * h, base = buf >> 2, s, i;
  for (i = 0; i < n; i++) {
    s = HEAPU32[base + i];
    dst[i] = 0xFF000000 | ((s & 0xFF) << 16) | (s & 0xFF00) | ((s >> 16) & 0xFF);
  }
  Module.dgCtx.putImageData(Module.dgImg, 0, 0);
  if (Module.onFrame) Module.onFrame();
});

void DG_Init() {}

void DG_DrawFrame()
{
  js_draw(DG_ScreenBuffer, DOOMGENERIC_RESX, DOOMGENERIC_RESY);
}

void DG_SleepMs(uint32_t ms) { (void)ms; }   // Hauptschleife per requestAnimationFrame, nie blockieren

uint32_t DG_GetTicksMs()
{
  return (uint32_t)emscripten_get_now();
}

int DG_GetKey(int* pressed, unsigned char* doomKey)
{
  if (s_KeyQueueReadIndex == s_KeyQueueWriteIndex)
    return 0;
  unsigned short keyData = s_KeyQueue[s_KeyQueueReadIndex];
  s_KeyQueueReadIndex = (s_KeyQueueReadIndex + 1) % KEYQUEUE_SIZE;
  *pressed = keyData >> 8;
  *doomKey = keyData & 0xFF;
  return 1;
}

void DG_SetWindowTitle(const char * title) { (void)title; }

// ------------------------------------------------------------------ Ton: Soundeffekte ueber Web Audio (kein SDL)
// DMX-Lump: 0x0003, Abtastrate (u16), Laenge (u32), 16 Fuellbytes, 8-Bit-PCM unsigned, 16 Fuellbytes.
// JavaScript (doom.html, Module.sfx*) dekodiert jeden Lump einmal zu einem AudioBuffer und spielt ihn je Kanal.
#ifdef FEATURE_SOUND
#include <stdio.h>
#include "i_sound.h"
#include "w_wad.h"
#include "z_zone.h"

int use_libsamplerate = 0;
float libsamplerate_scale = 0.65f;
static boolean sfx_prefix;

EM_JS(void, js_sfx_start, (int ch, unsigned char *data, int len, int rate, int vol, int sep, int lump), {
  if (Module.sfxStart) Module.sfxStart(ch, HEAPU8.subarray(data, data + len), rate, vol, sep, lump);
});
EM_JS(void, js_sfx_params, (int ch, int vol, int sep), { if (Module.sfxParams) Module.sfxParams(ch, vol, sep); });
EM_JS(void, js_sfx_stop, (int ch), { if (Module.sfxStop) Module.sfxStop(ch); });
EM_JS(int, js_sfx_playing, (int ch), { return Module.sfxPlaying ? (Module.sfxPlaying(ch) ? 1 : 0) : 0; });

static boolean S_Init(boolean use_sfx_prefix) { sfx_prefix = use_sfx_prefix; return true; }
static void S_Shutdown(void) {}
static int S_GetSfxLumpNum(sfxinfo_t *sfx)
{
  char name[9];
  if (sfx->link != NULL)
    sfx = sfx->link;
  snprintf(name, sizeof(name), sfx_prefix ? "ds%s" : "%s", sfx->name);
  return W_CheckNumForName(name);
}
static void S_Update(void) {}
static void S_UpdateSoundParams(int ch, int vol, int sep) { js_sfx_params(ch, vol, sep); }
static int S_StartSound(sfxinfo_t *sfx, int ch, int vol, int sep)
{
  int lump = sfx->lumpnum, len;
  unsigned char *d;
  if (lump < 0 || W_LumpLength(lump) < 32)
    return -1;
  d = W_CacheLumpNum(lump, PU_STATIC);
  len = d[4] | (d[5] << 8) | (d[6] << 16) | (d[7] << 24);
  if (d[0] != 3 || len <= 32 || len + 8 > (int)W_LumpLength(lump))
    return -1;
  js_sfx_start(ch, d + 24, len - 32, d[2] | (d[3] << 8), vol, sep, lump);
  return ch;
}
static void S_StopSound(int ch) { js_sfx_stop(ch); }
static boolean S_SoundIsPlaying(int ch) { return js_sfx_playing(ch) != 0; }
static void S_CacheSounds(sfxinfo_t *sounds, int num) { (void)sounds; (void)num; }

static snddevice_t sfx_devices[] = { SNDDEVICE_SB, SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER,
                                     SNDDEVICE_SOUNDCANVAS, SNDDEVICE_AWE32 };
sound_module_t DG_sound_module = {
  sfx_devices, sizeof(sfx_devices) / sizeof(*sfx_devices),
  S_Init, S_Shutdown, S_GetSfxLumpNum, S_Update, S_UpdateSoundParams, S_StartSound, S_StopSound,
  S_SoundIsPlaying, S_CacheSounds,
};

// Musik: (noch) keine - Doom-Musik braucht einen MIDI/OPL-Synthesizer
static boolean M_Init(void) { return false; }
static void M_Nop(void) {}
static void M_Vol(int v) { (void)v; }
static void *M_Reg(void *data, int len) { (void)data; (void)len; return NULL; }
static void M_Unreg(void *h) { (void)h; }
static void M_Play(void *h, boolean l) { (void)h; (void)l; }
static boolean M_Playing(void) { return false; }
music_module_t DG_music_module = {
  sfx_devices, sizeof(sfx_devices) / sizeof(*sfx_devices),
  M_Init, M_Nop, M_Vol, M_Nop, M_Nop, M_Reg, M_Unreg, M_Play, M_Nop, M_Playing, M_Nop,
};

void I_InitTimidityConfig(void) {}
#endif

int main(int argc, char **argv)
{
  doomgeneric_Create(argc, argv);
  emscripten_set_main_loop(doomgeneric_Tick, 0, 1);
  return 0;
}
