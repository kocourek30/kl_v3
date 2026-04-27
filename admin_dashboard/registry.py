REGISTERED_TASKS = (
    {
        "slug": "reset-monthly-accounts",
        "name": "Nulování kont v debetu",
        "category": "users",
        "command_name": "reset_monthly_accounts",
        "description": "Vynuluje záporné zůstatky u uživatelů s povoleným debetem.",
        "expected_interval_hours": 24 * 31,
    },
    {
        "slug": "mark-unpicked-orders",
        "name": "Označit nevyzvednuté objednávky",
        "category": "orders",
        "command_name": "mark_unpicked_orders",
        "description": "Domarkuje staré objednávky jako nevyzvednuté podle data výdeje.",
        "expected_interval_hours": 24,
    },
    {
        "slug": "menu-autoimport-datax",
        "name": "Autoimport jídelníčku (DATAx)",
        "category": "menu",
        "command_name": "run_datax_autoimport",
        "description": "Načte aktuální a příští měsíc z DATAx DBF bez potřeby ručního zásahu.",
        "expected_interval_hours": 6,
        "default_options": {
            "months_ahead": 1,
        },
    },
    {
        "slug": "price-recalculation-link",
        "name": "Přepočet cen objednávek",
        "category": "orders",
        "description": "Otevře admin nástroj na přepočet cen a audit změn.",
        "is_quick_link": True,
        "allow_manual_run": False,
        "target_url_name": "admin:objednavky_order_price_recalculation",
    },
)
