@echo off
verify on
pkzip.exe -3 -+ -- -= -) -a -P %1 @%2
if errorlevel 1 goto 1
exit
:1
echo chyba > 1
exit
