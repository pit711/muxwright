# -*- coding: utf-8 -*-
"""Analog-TV PAL (B/G, D/K, I) fuer den DAB-Lab-Runner (mode = "pal").

gen.py     Erzeugung: Standbild-Schleife (FFT-Verfahren aus pluto_fw/mod/tools/pal_bg_gen.py, am LG-TV erprobt) und
           Strom-Encoder fuer bewegtes Video (Bild fuer Bild, FIR-Restseitenbandfilter, mehrere Prozesse)
demod.py   Pruefdemodulator: Huellkurve -> Zeilenraster -> PNG, Pegel, Ton (FM-Demodulation), Vergleich mit dem Sollbild
cli.py     render / check / live / validate
"""
