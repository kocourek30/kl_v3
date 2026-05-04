import json

from django.core.management.base import BaseCommand, CommandError

from licencovani.services import build_signed_license


class Command(BaseCommand):
    help = "Vygeneruje podepsaný licenční soubor pro zákazníka."

    def add_arguments(self, parser):
        parser.add_argument("--private-key", required=True, help="Cesta k Ed25519 private_key.pem")
        parser.add_argument("--output", required=True, help="Výstupní JSON soubor licence")
        parser.add_argument("--license-id", required=True)
        parser.add_argument("--customer-name", required=True)
        parser.add_argument("--organization-name", default="")
        parser.add_argument("--license-type", default="subscription")
        parser.add_argument("--instance-id", default="")
        parser.add_argument("--valid-from", required=True)
        parser.add_argument("--valid-until", required=True)
        parser.add_argument("--support-until", required=True)
        parser.add_argument("--grace-until", default="")
        parser.add_argument("--modules", default="", help="Čárkou oddělené moduly, např. ankety,sklad,pokladna")
        parser.add_argument("--allowed-terminals", type=int, default=1)
        parser.add_argument("--notes", default="")

    def handle(self, *args, **options):
        modules = [item.strip() for item in options["modules"].split(",") if item.strip()]
        payload = {
            "license_id": options["license_id"],
            "customer_name": options["customer_name"],
            "organization_name": options["organization_name"],
            "license_type": options["license_type"],
            "instance_id": options["instance_id"] or None,
            "valid_from": options["valid_from"],
            "valid_until": options["valid_until"],
            "support_until": options["support_until"],
            "grace_until": options["grace_until"] or None,
            "modules": modules,
            "allowed_terminals": options["allowed_terminals"],
            "notes": options["notes"],
        }
        try:
            data = build_signed_license(payload, options["private_key"])
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        with open(options["output"], "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        self.stdout.write(self.style.SUCCESS(f"Licence uložená do {options['output']}"))

