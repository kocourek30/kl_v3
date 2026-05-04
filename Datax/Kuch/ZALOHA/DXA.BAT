@echo off
prompt Navrat prikazem EXIT $p$g$g
if not exist UVOD.FXP goto nejedu
cls
echo.
echo.
ECHO              O K A M Z I K    P R O S I M,    C E K E J T E
ECHO.
ECHO        S T A R T U J E    P R O G R A M    A L E X O    verze 4.004
echo.
echo.
echo               ßßß      ßß        ßßßßßß    ßß    ßß     ßßßßß 
echo              ßß ßß     ßß        ßß         ßß  ßß     ßß   ßß
echo             ßßßßßßß    ßß        ßßßßß        ßß       ßß   ßß
echo             ßß   ßß    ßß        ßß         ßß  ßß     ßß   ßß
echo             ßß   ßß    ßßßßßß    ßßßßßß    ßß    ßß     ßßßßß 
echo.
echo.
echo                                 F I R M Y
echo.
echo.
echo             ßßßßßß       ßßß     ßßßßßß     ßßß       ßß    ßß
echo             ßß   ßß     ßß ßß      ßß      ßß ßß       ßß  ßß 
echo             ßß   ßß    ßßßßßßß     ßß     ßßßßßßß        ßß  
echo             ßß   ßß    ßß   ßß     ßß     ßß   ßß      ßß  ßß
echo             ßßßßßß     ßß   ßß     ßß     ßß   ßß     ßß    ßß
if exist *.tmp del *.tmp > nul
foxfree.com
foxr -t uvod.fxp
cls
echo.
echo.
echo.
ECHO           P R O G R A M   A L E X O   verze 4.004   U K O N C E N
echo.
echo                M U Z E T E    V Y P N O U T    P O C I T A C
echo.
echo                  D E K U J I    Z A    S P O L U P R A C I
echo.
echo.
echo.
echo.
echo               ßßß      ßß        ßßßßßß    ßß    ßß     ßßßßß 
echo              ßß ßß     ßß        ßß         ßß  ßß     ßß   ßß
echo             ßßßßßßß    ßß        ßßßßß        ßß       ßß   ßß
echo             ßß   ßß    ßß        ßß         ßß  ßß     ßß   ßß
echo             ßß   ßß    ßßßßßß    ßßßßßß    ßß    ßß     ßßßßß 
echo.
echo.
goto konec
:NEJEDU
cls
ECHO                               L I T U J I,
ECHO.
ECHO                P R O G R A M    A L E X O    verze 4.004
echo.
echo.
echo.
echo               ßßß      ßß        ßßßßßß    ßß    ßß     ßßßßß 
echo              ßß ßß     ßß        ßß         ßß  ßß     ßß   ßß
echo             ßßßßßßß    ßß        ßßßßß        ßß       ßß   ßß
echo             ßß   ßß    ßß        ßß         ßß  ßß     ßß   ßß
echo             ßß   ßß    ßßßßßß    ßßßßßß    ßß    ßß     ßßßßß 
echo.
echo.
echo.
ECHO                         N E L Z E    S P U T I T
ECHO.
:konec
prompt $p$g
