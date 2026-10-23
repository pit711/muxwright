@echo off
rem DAB-Lab: Pluto-Firmware per DFU in den RAM booten (Flash bleibt unveraendert).
rem Ablauf: Pluto per SSH in den U-Boot-DFU-RAM-Modus bringen (device_reboot ram -> U-Boot "ramboot_verbose"
rem         -> dfu_ram), dann pluto.dfu mit dfu-util laden, dann "detach" -> U-Boot startet das Image.
rem Aufruf: boot_ram.cmd [pfad\zu\pluto.dfu]   (Standard: out\pluto.dfu)
setlocal
cd /d "%~dp0"
set IMG=%~1
if "%IMG%"=="" set IMG=out\pluto.dfu
if not exist "%IMG%" ( echo Datei %IMG% fehlt & exit /b 1 )
set DFU=tools\dfu-util-win64\dfu-util.exe -d 0456:b673,0456:b674
if not exist "tools\dfu-util-win64\dfu-util.exe" ( echo dfu-util.exe fehlt & exit /b 1 )

echo [1/4] Pluto in den DFU-RAM-Modus (device_reboot ram) ...
wsl -d dablab -u root -- sshpass -p analog ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 root@192.168.2.1 "sync; /usr/sbin/device_reboot ram" 2>nul
echo       warte auf USB-DFU-Geraet 0456:b674 ...
set /a N=0
:wait
ping -n 2 127.0.0.1 >nul
%DFU% -l 2>nul | findstr /i "firmware.dfu" >nul && goto found
set /a N+=1
if %N% LSS 40 goto wait
echo FEHLER: kein DFU-Geraet gefunden. Treiber? (Geraet "PlutoSDR DFU" braucht WinUSB, siehe README Recovery)
exit /b 2
:found
echo [2/4] DFU-Geraet gefunden:
%DFU% -l | findstr /i "firmware.dfu"
echo [3/4] lade %IMG% in den RAM (fit_load_address 0x2080000) ...
%DFU% -a firmware.dfu -D "%IMG%"
if errorlevel 1 ( echo FEHLER beim DFU-Download & exit /b 3 )
echo [4/4] detach -> U-Boot bootet das RAM-Image
%DFU% -a firmware.dfu -e
if errorlevel 1 ( echo FEHLER beim Detach - Pluto steht noch im DFU-Modus, Befehl wiederholen: %DFU% -a firmware.dfu -e & exit /b 4 )
echo Fertig. In ca. 20 s meldet sich der Pluto unter 192.168.2.1 (iio_info -u ip:192.168.2.1, http://192.168.2.1:8080/api/status).
echo Zurueck zur Flash-Firmware: einfach Strom/USB trennen oder "device_reboot reset".
endlocal
