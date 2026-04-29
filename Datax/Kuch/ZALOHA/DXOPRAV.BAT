@echo off
cls
echo Testuji parametry
if %1.==.   goto standd
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
:standd
set dxdsk=C:
goto inadr
:ok1
set dxdsk=%1
:inadr
if %2.==.   goto standa
set dxadr=%2
goto ok2
:standa
set dxadr=DATAX\KUCH
:ok2
echo Parametry OK
echo.
echo Testuji disketu
if not exist dx_k.opr goto nedisk
echo Disketa OK
echo.
echo Testuji instalaci programu KUCHYNKA v %dxdsk%\%dxadr%
if not exist %dxdsk%\%dxadr%\nul goto neinst
if not exist %dxdsk%\%dxadr%\pkunzip.exe goto neinst
if not exist %dxdsk%\%dxadr%\datax.fxp goto neinst
if not exist %dxdsk%\%dxadr%\licence.fxp goto neinst
if not exist %dxdsk%\%dxadr%\plachta.fxp goto neinst
if not exist %dxdsk%\%dxadr%\uvod.fxp goto neinst
if not exist %dxdsk%\%dxadr%\sys_info.fxp goto neinst
if not exist %dxdsk%\%dxadr%\install.fpt goto neinst
if not exist %dxdsk%\%dxadr%\dbf\c013.dbf goto neinst
if not exist %dxdsk%\%dxadr%\dxk.bat goto neinst
echo Instalace programu KUCHYNKA v %dxdsk%\%dxadr% je OK
echo.
echo          ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo          Û   Oprava starsi verze programu KUCHYNKA na verzi x.04    Û
echo          Û                                                          Û
echo          Û    Nahravani nove verze programu z instalacni diskety    Û
echo          Û   lze nyni prerusit stisknutim kombinace klaves Ctrl+C.  Û
echo          Û                                                          Û
echo          Û                  Chcete-li pokracovat,                   Û
echo          Û              stisknete libovolnou klavesu.               Û
echo          ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
pause > nul
echo Nahravam novou verzi programu z diskety do %dxdsk%\%dxadr%
echo Cekejte, prosim
%dxdsk%\%dxadr%\pkunzip.exe -o -d dx_k.opr %dxdsk%\%dxadr%
if errorlevel 1 goto 01chyba
If exist %dxdsk%\%dxadr%\dbf\foxuser.dbf copy %dxdsk%\%dxadr%\dbf\foxuser.* %dxdsk%\%dxadr% > nul
If exist %dxdsk%\%dxadr%\dbf\foxuser.dbf del  %dxdsk%\%dxadr%\dbf\foxuser.* > nul
copy %dxdsk%\%dxadr%\dtx\c20.fpt %dxdsk%\%dxadr%\dbf > nul
copy %dxdsk%\%dxadr%\dtx\c20.dtx %dxdsk%\%dxadr%\dbf\c20.dbf > nul
ECHO 
echo Nahravani nove verze OK
echo.
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û  Nova verze programu KUCHYNKA x.04 byla uspesne  Û
echo              Û                                                  Û
echo              Û              I N S T A L O V A N A.              Û
echo              Û                                                  Û
echo              Û                                                  Û
echo              Û           STISKNETE LIBOVOLNOU KLAVESU           Û
echo              Û                                                  Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
echo.
goto end
:01chyba
ECHO 
echo                ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo                Û                                              Û
echo                Û   Chyba pri nahravani nove verze programu!   Û
echo                Û                                              Û
echo                Û           Opakujte spusteni davky            Û
echo                Û      nebo volejte dodavatele programu.       Û
echo                Û                                              Û
echo                ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:nedisk
ECHO 
echo                ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo                Û          Toto neni spravna disketa!          Û
echo                Û                                              Û
echo                Û     Vlozte do mechaniky spravnou disketu     Û
echo                Û          a opakujte spusteni davky.          Û
echo                Û                                              Û
echo                ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
goto end
:err1
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û    Chybne zadane parametry pri spusteni davky!   Û
echo              Û  Opakujte spusteni davky se spravnymi parametry: Û
echo              Û                                                  Û
echo              Û          syntaxe:  DXOPRAV disk adresar          Û
echo              Û                                                  Û
echo              Û  disk ...... oznaceni disku, kde je instalovana  Û
echo              Û              starsi verze programu KUCHYNKA      Û
echo              Û              Jmeno disku zadejte vc. dvojtecky   Û
echo              Û  adresar ... jmeno adresare, kde je instalovana  Û
echo              Û              starsi verze programu KUCHYNKA      Û
echo              Û              Neuvadejte, je-li progr.instalovany Û
echo              Û              ve standardnim adresari DATAX\KUCH  Û
echo              Û                                                  Û
echo              Û  Napr.: DXOPRAV D:                               Û
echo              Û         - oprava starsi verze programu KUCHYNKA, Û
echo              Û           ktery je instalovany na disku D:       Û
echo              Û           ve standardnim adresari DATAX\KUCH     Û
echo              Û         DXOPRAV C: KUCHYNKA                      Û
echo              Û         - oprava starsi verze programu KUCHYNKA, Û
echo              Û           ktery je instalovany na disku C:       Û
echo              Û           v adresari KUCHYNKA                    Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
goto end
:neinst
ECHO 
echo              ÛßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßßÛ
echo              Û          Starsi verze programu KUCHYNKA          Û
echo              Û                                                  Û
echo              Û                na pevnem disku %dxdsk%                Û
echo              Û                                                  Û
echo              Û            NENI SPRAVNE INSTALOVANA.             Û
echo              Û                                                  Û
echo              Û           !!!  Nemohu  pokracovat  !!!           Û
echo              Û                                                  Û
echo              Û  Opakujte spusteni davky a zadejte spravny disk  Û
echo              Û  a adresar, kde je program KUCHYNKA instalovany. Û
echo              ÛÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÜÛ
ECHO 
:end
set dxdsk=
set dxadr=
echo Stisknete libovolnou klavesu
pause > nul
c:
cd\
