:cykl
@Echo on
regina stop.cfg aaa.dat aaa.txt 1 1
regina perclear.cfg aaa.dat aaa.txt 1 1
regina obedq.cfg aaa.dat aaa.txt 1 1
choice /CY /T:Y,5
regina obedy.cfg ooo.dat ooo.txt 201 1
type rexcom.sta
copy rexcom.kum+rexcom.sta rexcom.ttt
del rexcom.kum
ren rexcom.ttt rexcom.kum
regina start.cfg aaa.dat aaa.txt 1 1
choice /CY /T:Y,5
goto cykl