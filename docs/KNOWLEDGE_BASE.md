# Knowledge Base pro SOUzklikni jidlo

Tento projektový portál je stavěný přímo z Markdown dokumentace v repu.

## Co zahrnuje

- root `README.md`
- provozní a deployment dokumenty v `docs/`
- doplněné přehledy jednotlivých Django aplikací v `docs/apps/`

## Jak číst dokumentaci

- `docs/ADMIN_MANUAL.md` pro správu a provoz
- `docs/UZIVATELSKY_NAVOD.md` pro běžný provoz
- `docs/deployment` a `DEPLOY*.md` pro nasazení
- `docs/apps/` pro doménové moduly projektu

## Poznámka k serveru

Portál je připravený pro nasazení za reverse proxy tak, aby běžel pod `/kliknijidlo/` na VPN serveru `10.66.0.1`.

