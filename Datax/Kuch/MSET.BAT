@echo off
echo.
echo Cekejte, prosim. V zadnem pripade NEVYPINEJTE POCITAC
echo Nastavuji parametry varenych jidel
echo - maximalni pocty varenych chodu jednotlivych jidel po dnech
echo - limity pro ruseni objednavek
echo - limity pro objednavani chodu
echo.
regina.exe MEALSET.CFG MEALSET.DAT MEALSET.TXT 1 1
regina.exe GSET.CFG GSET.DAT GSET.TXT 1 1
