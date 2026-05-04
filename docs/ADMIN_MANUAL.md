# KlikniJídlo – Administrátorský manuál (podrobný)

Tento dokument je detailní provozní a technický manuál pro administraci systému KlikniJídlo.

Navazuje na uživatelský návod:
- [UZIVATELSKY_NAVOD.md](C:/SOUz_production/docs/UZIVATELSKY_NAVOD.md)

---

## 1. Cíl a rozsah

Manuál popisuje:
- správu rolí a oprávnění,
- správu uživatelů, jídel, jídelníčků a objednávek,
- importní procesy (Datax),
- výdej a odhlášky,
- provozní dashboardy,
- údržbu, diagnostiku a řešení incidentů.

---

## 2. Architektura systému (prakticky)

### Klíčové domény
- `users` – uživatelé, skupiny, vklady, konto.
- `jidelnicek` – jídelníčky, jídla, položky jídelníčku.
- `objednavky` – objednávky a položky objednávek.
- `vydej` + `vydej_frontend` – výdej a RFID/terminál.
- `provoz_jidelny` – provozní dashboard obsluhy.
- `ankety` – hodnocení jídel a měsíční volba menu.
- `admin_dashboard` – řízení přístupů, role-matrix, admin operace.

### Základní princip
- Provoz je orientovaný na **odhlášky**.
- Objednávky se mohou zakládat hromadně/importem.
- Výdej probíhá přes terminál/RFID a musí vytvářet účtenku.

---

## 3. Role, skupiny, oprávnění

### 3.1 Doporučení
- Nepřidělovat oprávnění ručně jednotlivým uživatelům.
- Používat role přes Django `Group`.
- Řídit přístup dvěma vrstvami:
1. model permissions (`view/add/change/delete`),
2. přístupové úrovně admin oblastí (`view/write/control`) přes `AdminViewAccess`.

### 3.2 Role obsluhy jídelny
Role: `Role • Obsluha jídelny`

Je nastavována commandem:
- `python manage.py setup_obsluha_jidelny`

Podporuje i vytvoření účtu:
- `python manage.py setup_obsluha_jidelny --create-user`

Default účet:
- username: `obsluha.jidelny`
- heslo: `Obsluha123!` (dočasné)
- povinná změna hesla při prvním přihlášení

### 3.3 Jak role interně funguje
Konfigurace je v:
- `users/management/commands/setup_obsluha_jidelny.py`

Manuál pro správce:
- Jakákoli změna role = upravit command + znovu spustit command.
- Tím se role přegeneruje konzistentně pro všechny členy.

---

## 4. Admin navigace a UX režim

### 4.1 Top menu
- Výchozí odkaz `Dashboard` je nahrazen odkazem `Provozní dashboard`.
- Duplicitní odkazy jsou odstraněné.

### 4.2 Sidebar
- Některé modely/app sekce se skrývají přes `JAZZMIN_SETTINGS["hide_models"]`.
- Příklad: skrytí app sekce „Provoz jídelny“ při zachování top odkazu na dashboard.

### 4.3 Kde se to nastavuje
- `kliknijidlo/settings.py`
  - `JAZZMIN_SETTINGS["topmenu_links"]`
  - `JAZZMIN_SETTINGS["hide_models"]`
  - `JAZZMIN_SETTINGS["icons"]`

---

## 5. Uživatelé a bezpečnost

### 5.1 Přihlášení
- Username je case-insensitive (uživatelé na mobilech).
- Novým/importovaným účtům lze vynutit změnu hesla:
  - pole `must_change_password` na `users.CustomUser`.

### 5.2 Povinná změna hesla
- Middleware: `users.middleware.ForcePasswordChangeMiddleware`.
- Pokud uživatel nemá změněné heslo, je přesměrován do formuláře změny.

### 5.3 Co dělat při chybě `column ... must_change_password does not exist`
Příčina:
- model upraven, ale databáze nemá aplikovanou migraci.

Náprava:
1. `python manage.py showmigrations users`
2. `python manage.py migrate users`
3. restart aplikace

---

## 6. Importy Datax – provozní postup

### 6.1 Zásada
- Před importem vždy záloha DB.
- Import provádět v definovaném pořadí.
- Po importu validace v adminu.

### 6.2 Zdroj dat
- Primární zdroj je složka `datax` v projektu (aktuálně používaná větev dat).
- Ignorovat historické/backup větve, pokud není cílem historická migrace.

### 6.3 Obecný importní řetězec
1. Import uživatelů (strávníci, personál).
2. Import jídel.
3. Import jídelníčků.
4. Slučování/sjednocení položek dle logiky projektu.
5. Kontrola výsledku (jídla, jídelníček, dostupnost pro role).

### 6.4 Uživatelské mapování (aktuální projektová logika)
- Username: `jmeno.prijmeni`
- Osobní číslo začínající `9` → učitel/personál
- Ostatní → student
- `id_medium` mapovat do pole `identifikacni_medium`
- Heslo po importu: osobní číslo (s následnou povinnou změnou hesla)

### 6.5 Jídelníčky a jídla
- Každé jídlo držet v systému jednou.
- Při importu jídelníčku inteligentně slučovat položky stejného jídla.
- U snídaní použita logika A/B tam, kde je potřeba odhlašovat celek.

---

## 7. Objednávky, odhlášky a výdej

### 7.1 Provozní model
- Systém je veden jako režim odhlášek.
- Uživatel vidí objednaný den a ruší den jako celek (ne jednotlivé položky).

### 7.2 Kritická pravidla
- Historické dny nesmí být objednatelné.
- Po změně měsíce v kalendáři se musí vybrat první objednatelný jídelníček.
- Měsíční view zobrazuje jen reálně objednatelné dny.

### 7.3 Výdej přes RFID
- Endpointy terminálu volají služby výdeje.
- Po výdeji vzniká/aktualizuje se `VydejniUctenka`.
- V případě chyb výdeje kontrolovat:
  - dostupnost RFID bridge,
  - aktuální výdejní časy,
  - stav objednávky (`objednano`, `zalozena-obsluhou`, `castecne-vydano`).

---

## 8. Provozní dashboard

### 8.1 Cílový účel
- Rychle ukázat, co se dnes vaří/vydává.
- Přehled výdejních oken a porcí.
- Tisk PDF odhlášek (vedení + kuchyň).

### 8.2 Auto refresh
- Dashboard se obnovuje periodicky na pozadí (bez ručního refresh).

### 8.3 Kuchyňské okno
- Samostatný vstup z provozního dashboardu.
- Upravený vzhled sjednocený s ostatními admin dashboardy.

---

## 9. Ankety

### 9.1 Rozdělení
- Hodnocení jídel.
- Měsíční volba menu.

### 9.2 Aktuální zjednodušení
- Ve vyhodnocení je zobecněný přehled.
- Detailní rozpad podle druhů jídel je dočasně vypnutý.
- Přidán souhrn měsíční volby menu do hlavního reportu.

---

## 10. Styling a šablony

### 10.1 Kde se primárně styluje admin
- `static/css/custom-admin.css`

### 10.2 Doporučení pro další úpravy
- Nepoužívat náhodné inline styly.
- Zachovat:
  - jednotný spacing,
  - stejnou tonalitu zelená/oranžová,
  - stejné card/chip/button patterny.

### 10.3 Specifické dashboard CSS
- `static/provoz_jidelny/css/dashboard.css`
- další app-specific CSS podle modulu.

---

## 11. Incident response – typické chyby a řešení

### 11.1 `relation ... does not exist`
Příčina:
- chybějící migrace modelu.

Řešení:
1. `python manage.py showmigrations`
2. `python manage.py migrate`
3. restart služby

### 11.2 `ProgrammingError column ... does not exist`
Příčina:
- nasazený kód je napřed proti DB schématu.

Řešení:
1. identifikovat app a migration state,
2. aplikovat migrace,
3. ověřit ručně dotčenou stránku.

### 11.3 PowerShell virtuální prostředí nejde aktivovat
Typická příčina:
- špatná cesta (`venv` vs `.venv`) nebo `Activate.ps1` neexistuje.

Diagnostika:
1. `Get-ChildItem -Name`
2. `Get-ChildItem .\venv\Scripts\Activate.ps1, .\.venv\Scripts\Activate.ps1`

Náprava:
- použít správnou cestu,
- případně vytvořit venv znovu.

---

## 12. Zálohy a obnova

### 12.1 Před zásahy
- vždy záloha DB před:
  - hromadnými importy,
  - čistkami dat,
  - většími migracemi.

### 12.2 Doporučení
- držet minimálně:
  - poslední denní zálohu,
  - týdenní snapshot,
  - zálohu před každým importním během.

### 12.3 Obnova
- Ověřit cílovou verzi kódu.
- Obnovit DB.
- Spustit migrace na cílový stav.
- Zkontrolovat klíčové flows:
  - login,
  - jídelníčky,
  - výdej,
  - admin reporty.

---

## 13. Nasazení do Dockeru (provozní checklist)

### 13.1 Předpoklady
- běží Docker/Compose,
- existuje `docker-compose.prod.yml`,
- existuje `.env.prod` ve správném adresáři.

### 13.2 Typický postup
1. Přesun do projektové složky.
2. Ověření přítomnosti souborů.
3. `docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build`
4. `python manage.py migrate`
5. kontrola zdraví aplikace.

### 13.3 Časté chyby
- „Couldn't find env file“:
  - nejsi v projektové složce,
  - `.env.prod` neexistuje.
- „No such file docker-compose.prod.yml“:
  - špatný working directory.

---

## 14. Doporučené pravidelné kontroly (týdně)

1. Stav licencování a přístupů.
2. Stav importních reportů.
3. Náhodná kontrola 1–2 dní jídelníčku proti zdroji.
4. Kontrola odhlášek a kuchyňských PDF sestav.
5. Kontrola anketního reportu.
6. Kontrola logů (`django.log`, `security.log`).

---

## 15. Runbook: „když ve čtvrtek jdeš s kůží na trh“

Krátký preflight:
1. Login role vedoucí.
2. Login role obsluha.
3. Provozní dashboard:
   - načte se bez chyby,
   - live data se refreshují.
4. Kuchyňský přehled:
   - správné datum,
   - porce a rozpis sedí.
5. Výdej (test položky):
   - vydání proběhne,
   - vznikne účtenka.
6. Odhlášky:
   - vygenerovat oba PDF reporty.
7. Ankety:
   - otevřít souhrn,
   - ověřit měsíční volbu menu.

---

## 16. Prostor pro lokální doplnění (doporučeno vyplnit)

- Produkční URL:
- Zodpovědná osoba:
- Kontakty na podporu:
- Kde jsou uložené zálohy:
- Kde je provozní .env:
- RFID bridge host/port:
- Postup eskalace incidentu:

---

## 17. Poznámka k dalšímu rozvoji

Doporučená další etapa:
- formální „Release checklist“,
- automatický smoke test klíčových URL,
- pravidelné exporty provozních reportů,
- audit log změn role/permission.

