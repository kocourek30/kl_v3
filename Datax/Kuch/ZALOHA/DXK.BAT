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
ok.com /J/L/G/S
foxr -t uvod.fxp
ok.com /U
if not exist upd_dtx.bat goto pokr1
call upd_dtx.bat
:pokr1
cls
goto konec
:NEJEDU
cls
ECHO                       P R O G R A M    K U C H Y N
ECHO                         N E L Z E    S P U T I T
ECHO.
:konec
