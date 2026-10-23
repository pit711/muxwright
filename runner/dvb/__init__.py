# -*- coding: utf-8 -*-
"""DVB-T/DVB-C-Transportstromerzeugung fuer den DAB-Lab-Runner.

Kette:  ffmpeg (Quellen, Codecs, PAT/PMT, muxrate) -> tsp (SDT/NIT/EIT/TDT/TOT, PCR, Regelung) -> stdout | UDP | Modulator

Einstieg: dablab_run.py delegiert bei mode in ("dvbt", "dvbc") an dvb.cli.run(args, host).
"""
