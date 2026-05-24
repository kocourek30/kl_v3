# `admin_dashboard`

Centrální vrstva pro oprávnění, moduly a admin UX.

## Účel

- registry admin modulů,
- přístupová práva podle rolí,
- mapování položek menu,
- přepínače viditelnosti aplikací a tasků.

## Modely

- `DashboardTask`
- `TaskRun`
- `AppModuleToggle`
- `AdminViewAccess`
- `AdminRoleMenuVisibility`

## Poznámka

Toto je důležitá vrstva pro správce. Neřeší business data, ale to, kdo co uvidí a spustí.

