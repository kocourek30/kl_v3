# KlikniJídlo – Uživatelský návod (provozní verze)

Tento návod je praktický průvodce pro každodenní provoz jídelny v aplikaci KlikniJídlo.

## 1. Role a přístupy

### Superadmin
- Má plný přístup do všech částí systému.
- Nastavuje role, oprávnění, importy, globální nastavení.

### Vedoucí jídelny
- Spravuje jídelníčky, jídla, objednávky, provozní dashboard, přehledy.
- Typicky pracuje v adminu přes provozní dashboard.

### Obsluha jídelny
- Primárně používá provozní dashboard a kuchyňský přehled.
- Má omezený admin podle nastavené role (`Role • Obsluha jídelny`).

## 2. Základní provozní tok

### Každý den
1. Otevřít `Provozní dashboard` v adminu.
2. Zkontrolovat dnešní objemy (porce, druhy jídel, výdejní okna).
3. Otevřít `Kuchyňské okno` (přehled pro kuchyni).
4. Průběžně sledovat změny odhlášek a stav výdeje.

### Výdej jídel
- Výdej je postavený na RFID/terminálu.
- Po vydání musí vzniknout výdejní účtenka (vazba je v systému implementovaná).

### Odhlášky
- Uživatelé primárně pracují v režimu odhlášek.
- Pro provoz lze tisknout PDF přehled odhlášek (pro vedení i pro kuchyň).

## 3. Jídelníčky a jídla

### Práce s jídelníčkem
- Jídelníčky se spravují v adminu v sekci `Jídelníček`.
- U importovaných dat se používá logika slučování položek a sjednocení názvů.

### Práce s jídly
- Jídla jsou v katalogu vedena jako sdílené položky.
- Karty jídel obsahují stav připravenosti (komponenty/suroviny/alergeny/výživa/fotka).

## 4. Importy (Datax)

### Co je zdroj pravdy
- Pro jídelníčky je zdroj pravdy Datax import.
- Importy běží přes připravené management commandy.

### Doporučený postup
1. Záloha DB.
2. Import uživatelů.
3. Import jídel a jídelníčků.
4. Kontrola v adminu.
5. Teprve poté navazující provozní operace.

## 5. Uživatelé a přihlášení

- Přihlášení ignoruje velikost písmen v username (kvůli mobilům/Android).
- U nových/importovaných účtů lze vynutit změnu hesla po prvním přihlášení.
- V admin horní části je zobrazováno jméno, příjmení a role.

## 6. Ankety

Ankety jsou rozdělené na:
- Hodnocení jídel.
- Měsíční volbu menu.

Ve vyhodnocení je nyní zjednodušený pohled:
- souhrn hodnocení,
- měsíční volba menu,
- bez detailního rozpadu podle druhů jídel (ten lze vrátit později).

## 7. Nejčastější provozní problémy

### „Nezobrazuje se to, co má“
- Zkontrolovat roli/skupiny uživatele.
- Zkontrolovat `AdminViewAccess` úrovně (view/write/control).

### „Po změně oprávnění pořád staré menu“
- Odhlásit/přihlásit uživatele.
- Ověřit, že role byla znovu synchronizována.

### „Chybí data v dashboardu“
- Ověřit datum výdeje a stav objednávek.
- Ověřit, že pro daný den existují objednatelné položky.

## 8. Doporučení pro provoz

- Měnit oprávnění přes role, ne ručně per-user.
- Před většími importy vždy udělat zálohu DB.
- Držet jeden jasný vstup do provozu (`Provozní dashboard`).
- Udržovat UI konzistentní (jednotná navigace, stejné akční vzory).

## 9. Rychlý onboarding obsluhy

1. Přihlásit se.
2. Otevřít `Provozní dashboard`.
3. V kuchyňském okně zkontrolovat denní přehled.
4. Pracovat s odhláškami a výdejem dle směny.
5. Na konci směny zkontrolovat stav výdeje a report odhlášek.

---

Pokud bude potřeba, navážeme druhým dokumentem:
- „Administrátorský návod“ (detail nastavení, importy, migrace, role, troubleshooting).
