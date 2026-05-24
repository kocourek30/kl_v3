# `objednavky`

Doménová vrstva pro objednávky, jejich přepočty a audit změn.

## Účel

- eviduje objednávky a jejich položky,
- sleduje RFID vazbu uživatele,
- drží logiku přepočtu cen,
- uchovává historii storna a změn stavu.

## Modely

- `Order`
- `OrderItem`
- `UserRFID`
- `PriceRecalculationLog`
- `PriceRecalculationDetail`
- `OrderCancellationLog`

## Poznámka

`objednavky` je doménově důležitá, ale uživatelský tok je dnes převážně obsluhovaný přes `jidelnicek`.

