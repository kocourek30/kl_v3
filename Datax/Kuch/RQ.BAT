:cykl
@Echo on
regina b_lok.cfg aaa.dat aaa.txt 1 1
regina clea_all.cfg aaa.dat aaa.txt 1 1
regina pis_all.cfg aaa.dat aaa.txt 1 1
choice /CY /T:Y,5
regina w_ctiall.cfg ooo.dat ooo.txt 201 1
type rexcom.sta
copy rexcom.kum+rexcom.sta rexcom.ttt
del rexcom.kum
ren rexcom.ttt rexcom.kum
choice /CY /T:Y,5
goto cykl