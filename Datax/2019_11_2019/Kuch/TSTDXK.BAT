@echo off
if not exist UVOD.FXP goto nejedu
rem cls
if exist start.bat del start.bat > nul
if not exist upd_dtx.bat goto pokr0
del upd_dtx.bat
rd upddtx
:pokr0
if exist *.tmp del *.tmp > nul

rem ok.com /i /+ /a /y /l /m03 /# /s /0

rem ok.com /i /+ /z /l /m03 /s /0 /g
rem pause

rem cs-vga.com
rem cea-keyb.com
foxfree.com
foxr -t uvod.fxp
if not exist upd_dtx.bat goto pokr1
call upd_dtx.bat
:pokr1
rem cls

rem ok.com /u

goto konec
:NEJEDU
rem cls
:konec
