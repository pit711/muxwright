#!/bin/bash
# Doom (doomgeneric + Freedoom) als reines JavaScript fuer HbbTV-Browser ohne WebAssembly (LG webOS 4.5 = Chrome 53).
# Aufruf in WSL: bash /mnt/g/DEEPSEEK/plutoTX/doom-hbbtv/build.sh   -> ~/doom-hbbtv/out/
set -e
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK=~/doom-hbbtv
SRC=$WORK/doomgeneric/doomgeneric
OUT=$WORK/out
source ~/emsdk/emsdk_env.sh >/dev/null 2>&1
mkdir -p $WORK/build $OUT
cp "$HERE/doomgeneric_hbbtv.c" $SRC/

OBJS="dummy am_map doomdef doomstat dstrings d_event d_items d_iwad d_loop d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random p_ceilng p_doors p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video doomgeneric doomgeneric_hbbtv"
# Ton: eigenes Soundmodul (doomgeneric_hbbtv.c, Web Audio); i_sound.c bindet SDL_mixer.h ein -> leerer Platzhalter
mkdir -p $WORK/stub && : > $WORK/stub/SDL_mixer.h
CFLAGS="-O3 -DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 -DFEATURE_SOUND -I$WORK/stub -Wno-everything"
for o in $OBJS; do
  [ $WORK/build/$o.o -nt $SRC/$o.c ] || emcc $CFLAGS -c $SRC/$o.c -o $WORK/build/$o.o
done

# WAD: Freedoom Phase 1, nur Episode 1, ohne Musik (wadtool.py) - fuer das Karussell so klein wie moeglich
python3 "$HERE/wadtool.py" $WORK/freedoom-0.13.0/freedoom1.wad --e1 $WORK/build/freedoom1.wad
cd $WORK/build
# Programm ohne Datenpaket: doom.html laedt WAD und JS als gepackte Stuecke (Karussell oder LAN) und legt die WAD
# vor dem Start per FS_createDataFile an; Speicherabbild ins JS eingebettet (eine Datei weniger)
emcc $CFLAGS $(for o in $OBJS; do echo $o.o; done) -o $WORK/build/doom.js   -sWASM=0 -sLEGACY_VM_SUPPORT=1 -sMIN_CHROME_VERSION=53 --memory-init-file 0   -sINITIAL_MEMORY=134217728 -sALLOW_MEMORY_GROWTH=0 -sSTACK_SIZE=1048576 -sFORCE_FILESYSTEM=1   -sEXPORTED_FUNCTIONS=_main,_dg_key -sEXPORTED_RUNTIME_METHODS=FS_createDataFile   -sINVOKE_RUN=1 -sEXIT_RUNTIME=0 -sENVIRONMENT=web
rm -rf $OUT && mkdir -p $OUT
python3 "$HERE/pack.py" $WORK/build/doom.js $WORK/build/freedoom1.wad $OUT
cp "$HERE/doom.html" "$HERE/inflate.js" $OUT/
ls -la $OUT; du -sh $OUT
