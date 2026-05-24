# `frontend`

Veřejný vstupní modul webu.

## Účel

- zobrazuje promo landing page,
- řeší RFID login obrazovku,
- poskytuje API pro přihlášení přes RFID terminál,
- obsluhuje odhlášení a přesměrování do dalších částí systému.

## Hlavní view

- `promo_home`
- `rfid_login_page`
- `rfid_login_api`
- `logout_view`

## Modely

- `LandingPage`
- `LandingBlock`

## Poznámka

Tento modul je vhodný jako vstupní vrstva, ale samotný provoz jídelny běží hlavně v `jidelnicek` a `vydej_frontend`.

