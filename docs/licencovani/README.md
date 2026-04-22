# Licencování KlikniJídlo

## Doporučený model

- proprietární licence k aplikaci
- offline podepsaný licenční soubor
- bez povinného online ověřování
- modulové licencování pro:
  - ankety
  - finance
  - fakturace
  - pokladna
  - sklad
- základní provozní části (`uživatelé`, `jídelníček`, `objednávky`, `výdej`, `dotace`, `nastavení`) zůstávají mimo licenční vypínání, aby licence nikdy nezpůsobila kolaps provozu
- ochranná lhůta po skončení platnosti, aby nedošlo k výpadku provozu

## Technický model

1. Vývojář vygeneruje Ed25519 klíče.
2. Zákazník nahlásí ID instalace.
3. Vývojář vystaví podepsaný `license.json`.
4. Zákazník licenci vloží do adminu.
5. Aplikace licenci ověří lokálně veřejným klíčem.
6. Volitelné moduly se odemykají podle seznamu v licenci.

## Umístění

- veřejný klíč: `data/licencovani/public_key.pem`
- podepsaná licence: v admin modelu `Licencování > Licence aplikace`

## Povinná pole payloadu

- `license_id`
- `customer_name`
- `license_type`
- `valid_from`
- `valid_until`
- `support_until`
- `modules`

## Volitelná pole

- `organization_name`
- `instance_id`
- `grace_until`
- `allowed_terminals`
- `notes`
