@echo off
if not exist UVOD.FXP goto nejedu
cls
if exist start.bat del start.bat > nul
if not exist upd_dtx.bat goto pokr0
del upd_dtx.bat
rd upddtx
:pokr0
if exist *.tmp del *.tmp > nul
foxfree.com
foxr -t uvod.fxp
if not exist upd_dtx.bat goto pokr1
call upd_dtx.bat
:pokr1
cls
goto konec
:NEJEDU
cls
:konec
