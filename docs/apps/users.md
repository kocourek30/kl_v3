# `users`

Uživatelská a účtová doména projektu.

## Účel

- vlastní `CustomUser`,
- drží identifikační médium pro RFID i mobilní identifikaci,
- spravuje vklady a účetní historii,
- řeší profil, účtenky a povinnou změnu hesla.

## Hlavní části

- modely: `StravovaciSkupina`, `Vklad`, `CustomUser`
- URL: profil, historie čerpání, historie účtu, PDF účtenky, logout
- middleware: vynucení změny hesla
- context processors: stav účtu pro šablony

## Provozní poznámka

Tady se nejčastěji řeší:

- import strávníků,
- reset měsíčních účtů,
- nastavení obsluhy jídelny,
- párování RFID karet.

