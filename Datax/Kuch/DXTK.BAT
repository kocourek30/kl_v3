@echo off
if not exist tran567.FXP goto nejedu
if exist ukey.com ukey.com
cls
echo.
echo.
echo              O K A M Z I K,    P R O S I M,    C E K E J T E
echo.
echo      STARTUJE PROGRAM PRO TRANSFORMACI DAT PUVODNIHO PROGRAMU "KUCHYN"
echo.
echo.
if exist *.tmp del *.tmp > nul
foxr -t tran567.fxp
if exist ukey.com ukey.com /u
cls
echo.
echo.
echo.
ECHO PROGRAM PRO TRANSFORMACI DAT PUVODNIHO PROGRAMU "KUCHYN"    U K O N C E N
echo.
echo             M U Z E T E    V Y P N O U T    P O C I T A C
echo.
echo               D E K U J I    Z A    S P O L U P R A C I
echo.
echo.
goto konec
:NEJEDU
cls
ECHO                                L I T U J I,
ECHO.
ECHO         PROGRAM PRO TRANSFORMACI DAT PUVODNIHO PROGRAMU "KUCHYN"
echo.
echo.
ECHO                         N E L Z E    S P U T I T
ECHO.
:konec
