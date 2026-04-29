@echo off
pkunzip.exe -3 -+ -- -) -d -o %1 %2 %3
if errorlevel 1 goto 1
exit
:1
echo chyba > 1
exit
