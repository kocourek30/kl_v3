# Docker deploy na `jidelna.kliknijidlo.cz`

Tento postup nasadí aktuální stav aplikace do Dockeru na NAS:
- `db` (PostgreSQL)
- `web` (Django + Gunicorn)
- `nginx` (HTTPS reverse proxy)
- volitelně `rfid` (`--profile rfid`)

## 1) DNS + certifikát

1. Nasměruj `A` záznam `jidelna.kliknijidlo.cz` na veřejnou IP NASu.
2. Na NASu připrav SSL certifikát (např. Let's Encrypt), aby existovaly soubory:
   - `/etc/letsencrypt/live/jidelna.kliknijidlo.cz/fullchain.pem`
   - `/etc/letsencrypt/live/jidelna.kliknijidlo.cz/privkey.pem`

## 2) Připrav produkční env

```bash
cp .env.prod.example .env.prod
```

Vyplň hlavně:
- `DJANGO_SECRET_KEY`
- `DB_PASSWORD`
- `DJANGO_ALLOWED_HOSTS`
- `CSRF_TRUSTED_ORIGINS`
- `SSL_CERT_PATH`, `SSL_KEY_PATH`

## 3) Build a spuštění

Bez RFID:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

S RFID (pokud má NAS fyzicky čtečku):

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile rfid up -d --build
```

## 4) Kontrola

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml ps
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f web
docker compose --env-file .env.prod -f docker-compose.prod.yml logs -f nginx
```

## 5) První admin

Po prvním běhu vytvoř admina:

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

## 6) Aktualizace při nové verzi

```bash
git pull
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

## Poznámky

- Produkční compose používá interní Docker volume pro DB/media/static/logs.
- `web` při startu automaticky spustí `migrate` a `collectstatic`.
- Nginx config je v `deploy/nginx/jidelna.conf`.
- Pokud RFID nepoužíváš, route `/socket.io/` může vracet chybu, ale běžný web poběží normálně.
