@echo off
cls
echo Testuji parametry
if %1.==c:. goto ok1
if %1.==d:. goto ok1
if %1.==C:. goto ok1
if %1.==D:. goto ok1
if %1.==e:. goto ok1
if %1.==f:. goto ok1
if %1.==E:. goto ok1
if %1.==F:. goto ok1
if %1.==g:. goto ok1
if %1.==h:. goto ok1
if %1.==i:. goto ok1
if %1.==j:. goto ok1
if %1.==k:. goto ok1
if %1.==l:. goto ok1
if %1.==m:. goto ok1
if %1.==n:. goto ok1
if %1.==o:. goto ok1
if %1.==p:. goto ok1
if %1.==q:. goto ok1
if %1.==r:. goto ok1
if %1.==s:. goto ok1
if %1.==t:. goto ok1
if %1.==u:. goto ok1
if %1.==v:. goto ok1
if %1.==w:. goto ok1
if %1.==x:. goto ok1
if %1.==y:. goto ok1
if %1.==z:. goto ok1
if %1.==G:. goto ok1
if %1.==H:. goto ok1
if %1.==I:. goto ok1
if %1.==J:. goto ok1
if %1.==K:. goto ok1
if %1.==L:. goto ok1
if %1.==M:. goto ok1
if %1.==N:. goto ok1
if %1.==O:. goto ok1
if %1.==P:. goto ok1
if %1.==Q:. goto ok1
if %1.==R:. goto ok1
if %1.==S:. goto ok1
if %1.==T:. goto ok1
if %1.==U:. goto ok1
if %1.==V:. goto ok1
if %1.==W:. goto ok1
if %1.==X:. goto ok1
if %1.==Y:. goto ok1
if %1.==Z:. goto ok1
goto err1
:ok1
echo Parametry OK
echo.
echo Testuji starou instalaci programu KUCHYN na disku %1
if not exist %1\KUCHYN\nul goto neinst
rem if not exist %1\KUCHYN\LEDEN\nul goto neinst
rem if not exist %1\KUCHYN\UNOR\nul goto neinst
rem if not exist %1\KUCHYN\BREZEN\nul goto neinst
rem if not exist %1\KUCHYN\DUBEN\nul goto neinst
rem if not exist %1\KUCHYN\KVETEN\nul goto neinst
rem if not exist %1\KUCHYN\CERVEN\nul goto neinst
rem if not exist %1\KUCHYN\CERVENEC\nul goto neinst
rem if not exist %1\KUCHYN\SRPEN\nul goto neinst
rem if not exist %1\KUCHYN\ZARI\nul goto neinst
rem if not exist %1\KUCHYN\RIJEN\nul goto neinst
rem if not exist %1\KUCHYN\LISTOPAD\nul goto neinst
rem if not exist %1\KUCHYN\PROSINEC\nul goto neinst
if not exist %1\KUCHYN\ARCHTISK\nul md %1\KUCHYN\ARCHTISK
echo Instalace programu KUCHYN verze 5.03 OK
echo.
echo Testuji disketu
if not exist pkzip.exe goto nedisk
echo Disketa OK
echo.
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û  Nahraje na disketu  mesicni uzaverky  ze stare  Û
echo              Û  verze  programu  KUCHYN,  ktery je instalovany  Û
echo              Û  na pevnem disku %1 v adresari \KUCHYN           Û
echo              Û                                                  Û
echo              Û Nahravani mesicnich uzaverek  ze stare instalace Û
echo              Û programu KUCHYN  na disketu lze v tomto okamziku Û
echo              Û prerusit stisknutim kombinace klaves Ctrl+C      Û
echo              Û                                                  Û
echo              Û              Chcete-li pokracovat,               Û
echo              Û          stisknete libovolnou klavesu.           Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
pause > nul
cls
verify on
echo Kopiruji stare mesicni uzaverky
echo Cekejte, prosim
attrib -R %1\KUCHYN\*.* /S
if exist %1\KUCHYN\ARCHTISK\*.dbf     del  %1\KUCHYN\ARCHTISK\*.dbf > nul
if exist %1\KUCHYN\LEDEN\QHK09.DBF    copy %1\KUCHYN\LEDEN\QHK09.DBF    %1\KUCHYN\ARCHTISK\QHK0901.DBF > nul
if exist %1\KUCHYN\UNOR\QHK09.DBF     copy %1\KUCHYN\UNOR\QHK09.DBF     %1\KUCHYN\ARCHTISK\QHK0902.DBF > nul
if exist %1\KUCHYN\BREZEN\QHK09.DBF   copy %1\KUCHYN\BREZEN\QHK09.DBF   %1\KUCHYN\ARCHTISK\QHK0903.DBF > nul
if exist %1\KUCHYN\DUBEN\QHK09.DBF    copy %1\KUCHYN\DUBEN\QHK09.DBF    %1\KUCHYN\ARCHTISK\QHK0904.DBF > nul
if exist %1\KUCHYN\KVETEN\QHK09.DBF   copy %1\KUCHYN\KVETEN\QHK09.DBF   %1\KUCHYN\ARCHTISK\QHK0905.DBF > nul
if exist %1\KUCHYN\CERVEN\QHK09.DBF   copy %1\KUCHYN\CERVEN\QHK09.DBF   %1\KUCHYN\ARCHTISK\QHK0906.DBF > nul
if exist %1\KUCHYN\CERVENEC\QHK09.DBF copy %1\KUCHYN\CERVENEC\QHK09.DBF %1\KUCHYN\ARCHTISK\QHK0907.DBF > nul
if exist %1\KUCHYN\SRPEN\QHK09.DBF    copy %1\KUCHYN\SRPEN\QHK09.DBF    %1\KUCHYN\ARCHTISK\QHK0908.DBF > nul
if exist %1\KUCHYN\ZARI\QHK09.DBF     copy %1\KUCHYN\ZARI\QHK09.DBF     %1\KUCHYN\ARCHTISK\QHK0909.DBF > nul
if exist %1\KUCHYN\RIJEN\QHK09.DBF    copy %1\KUCHYN\RIJEN\QHK09.DBF    %1\KUCHYN\ARCHTISK\QHK0910.DBF > nul
if exist %1\KUCHYN\LISTOPAD\QHK09.DBF copy %1\KUCHYN\LISTOPAD\QHK09.DBF %1\KUCHYN\ARCHTISK\QHK0911.DBF > nul
if exist %1\KUCHYN\PROSINEC\QHK09.DBF copy %1\KUCHYN\PROSINEC\QHK09.DBF %1\KUCHYN\ARCHTISK\QHK0912.DBF > nul

if exist %1\KUCHYN\LEDEN\QHK10.DBF    copy %1\KUCHYN\LEDEN\QHK10.DBF    %1\KUCHYN\ARCHTISK\QHK1001.DBF > nul
if exist %1\KUCHYN\UNOR\QHK10.DBF     copy %1\KUCHYN\UNOR\QHK10.DBF     %1\KUCHYN\ARCHTISK\QHK1002.DBF > nul
if exist %1\KUCHYN\BREZEN\QHK10.DBF   copy %1\KUCHYN\BREZEN\QHK10.DBF   %1\KUCHYN\ARCHTISK\QHK1003.DBF > nul
if exist %1\KUCHYN\DUBEN\QHK10.DBF    copy %1\KUCHYN\DUBEN\QHK10.DBF    %1\KUCHYN\ARCHTISK\QHK1004.DBF > nul
if exist %1\KUCHYN\KVETEN\QHK10.DBF   copy %1\KUCHYN\KVETEN\QHK10.DBF   %1\KUCHYN\ARCHTISK\QHK1005.DBF > nul
if exist %1\KUCHYN\CERVEN\QHK10.DBF   copy %1\KUCHYN\CERVEN\QHK10.DBF   %1\KUCHYN\ARCHTISK\QHK1006.DBF > nul
if exist %1\KUCHYN\CERVENEC\QHK10.DBF copy %1\KUCHYN\CERVENEC\QHK10.DBF %1\KUCHYN\ARCHTISK\QHK1007.DBF > nul
if exist %1\KUCHYN\SRPEN\QHK10.DBF    copy %1\KUCHYN\SRPEN\QHK10.DBF    %1\KUCHYN\ARCHTISK\QHK1008.DBF > nul
if exist %1\KUCHYN\ZARI\QHK10.DBF     copy %1\KUCHYN\ZARI\QHK10.DBF     %1\KUCHYN\ARCHTISK\QHK1009.DBF > nul
if exist %1\KUCHYN\RIJEN\QHK10.DBF    copy %1\KUCHYN\RIJEN\QHK10.DBF    %1\KUCHYN\ARCHTISK\QHK1010.DBF > nul
if exist %1\KUCHYN\LISTOPAD\QHK10.DBF copy %1\KUCHYN\LISTOPAD\QHK10.DBF %1\KUCHYN\ARCHTISK\QHK1011.DBF > nul
if exist %1\KUCHYN\PROSINEC\QHK10.DBF copy %1\KUCHYN\PROSINEC\QHK10.DBF %1\KUCHYN\ARCHTISK\QHK1012.DBF > nul

if not exist %1\KUCHYN\ARCHTISK\*.dbf goto nenico

echo Kopirovani souboru OK
echo.
echo Komprimuji mesicni uzaverky na disketu
echo Cekejte, prosim
if exist kuch503u.zip del kuch503u.zip
pkzip.exe -3 -+ -- -) kuch503u.zip %1\KUCHYN\ARCHTISK\*.dbf
if errorlevel 1 goto 01chyba
if not exist kuch503u.zip goto 01chyba
del %1\KUCHYN\ARCHTISK\*.dbf > nul
echo.
echo Komprimace souboru OK
echo.
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û  Disketa pro prenos mesicnich uzaverek ze stare  Û
echo              Û    do nove verze programu KUCHYN byla uspesne    Û
echo              Û                                                  Û
echo              Û                V Y T V O R E N A                 Û
echo              Û                                                  Û
echo              Û          STISKNETE LIBOVOLNOU KLAVESU.           Û
echo              Û                                                  Û
echo              Û   Tuto disketu pouzijete  pri transformaci dat   Û
echo              Û   do nove instalovane verze programu KUCHYNKA!   Û
echo              Û                                                  Û
echo              Û            Postupujte podle prirucky.            Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
echo.
goto end
:01chyba
del %1\KUCHYN\ARCHTISK\*.dbf > nul
ECHO 
echo                ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo                Û                                              Û
echo                Û   Chyba pri komprimaci souboru na disketu!   Û
echo                Û                                              Û
echo                Û           Opakujte spusteni davky.           Û
echo                Û                                              Û
echo                ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:nenico
ECHO 
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û                                                 Û
echo              Û  Ve stare instalaci programu KUCHYN verze 5.03  Û
echo              Û                                                 Û
echo              Û         NEJSOU ZADNE MESICNI UZAVERKY!          Û
echo              Û                                                 Û
echo              Û      Do noveho programu neni co prenaset.       Û
echo              Û                                                 Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
echo.
goto end
:nedisk
ECHO 
echo                ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo                Û          Toto neni spravna disketa!          Û
echo                Û                                              Û
echo                Û             Vlozte do mechaniky              Û
echo                Û              spravnou  disketu               Û
echo                Û          a opakujte spusteni davky.          Û
echo                Û                                              Û
echo                ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:err1
ECHO 
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û    Chybne zadane parametry pri spusteni davky!   Û
echo              Û                                                  Û
echo              Û  Opakujte spusteni davky se spravnymi parametry: Û
echo              Û                                                  Û
echo              Û               syntaxe: PRENESU disk              Û
echo              Û                                                  Û
echo              Û  disk ...... oznaceni disku, kde je instalovana  Û
echo              Û              stara verze programu KUCHYN - 5.03  Û
echo              Û              Jmeno disku zadejte vc. dvojtecky   Û
echo              Û                                                  Û
echo              Û                 Napr.: PRENESU C:                Û
echo              Û                                                  Û
echo              Û  Nahraje na disketu  mesicni uzaverky  ze stare  Û
echo              Û  verze  programu  KUCHYN,  ktery je instalovany  Û
echo              Û  na pevnem disku C: v adresari \KUCHYN           Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:neinst
ECHO 
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û         Stara verze programu KUCHYN 5.03         Û
echo              Û                                                  Û
echo              Û                na pevnem disku %1                Û
echo              Û                                                  Û
echo              Û             NENI SPRAVNE INSTALOVANA             Û
echo              Û                                                  Û
echo              Û           !!!  Nemohu  pokracovat  !!!           Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:end
verify off
ECHO 
echo Stisknete libovolnou klavesu
pause > nul
