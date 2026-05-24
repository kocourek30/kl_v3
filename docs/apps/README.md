# Přehled aplikací

Níže je stručná mapa jednotlivých Django modulů v projektu SOUzklikni jidlo.

## Doménové moduly

- `users` - uživatelé, profily, RFID identifikátory, vklady a přihlášení.
- `jidelnicek` - jídelníčky, katalog jídel, položky menu a importní běhy.
- `objednavky` - objednávky, položky objednávek a související výpočty.
- `dotace` - dotační politika, skupinová nastavení a výpočet dotací.
- `canteen_settings` - provozní časy, kontakty, výjimky a limity.
- `vydej` - doménová logika výdeje, účtenky a kuchyňské přehledy.
- `vydej_frontend` - terminálový dashboard, kiosk režim a RFID workflow.
- `vydej_jidel` - nastavení a časování výdeje jídel.
- `pokladna` - pokladna, doklady, uzávěrky a QR platby.
- `ankety` - hodnocení jídel a měsíční volba menu.
- `sklad` - sklad, šarže, pohyby, normy a spotřební koš.
- `provoz_jidelny` - provozní dashboard a navázané sestavy.
- `fakturace` - fakturační dávky a položky.
- `licencovani` - licenční klíče, podpisy a enforcement.
- `admin_dashboard` - role, přístupové matice a administrační registry.
- `finance` - finanční dashboard a související administrace.
- `frontend` - login, promo landing a veřejný vstupní bod.

## Co je důležité

- `jidelnicek` je uživatelský vstup pro objednávání.
- `vydej_frontend` je denní provozní vstup pro terminál a obsluhu.
- `admin_dashboard` drží přístupovou logiku pro správce.
- `sklad`, `pokladna`, `fakturace` a `dotace` tvoří ekonomicko-provozní vrstvu.

