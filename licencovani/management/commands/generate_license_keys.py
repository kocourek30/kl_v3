from django.core.management.base import BaseCommand

from licencovani.services import generate_keypair


class Command(BaseCommand):
    help = "Vygeneruje Ed25519 klíče pro offline licencování."

    def add_arguments(self, parser):
        parser.add_argument("--out-dir", default="data/licencovani", help="Cílová složka pro klíče.")

    def handle(self, *args, **options):
        private_path, public_path = generate_keypair(options["out_dir"])
        self.stdout.write(self.style.SUCCESS(f"Soukromý klíč: {private_path}"))
        self.stdout.write(self.style.SUCCESS(f"Veřejný klíč: {public_path}"))

