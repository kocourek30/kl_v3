import random
from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from ankety.models import AnketniOtazka, HodnoceniJidla, OdpovedHodnoceni
from objednavky.models import OrderItem


DEFAULT_QUESTIONS = [
    "Jak vám jídlo chutnalo?",
    "Byla porce dostatečná?",
    "Bylo jídlo správně teplé a čerstvé?",
    "Objednal/a byste si toto jídlo znovu?",
]

POSITIVE_NOTES = [
    "",
    "",
    "",
    "Výborné, dal/a bych si znovu.",
    "Porce byla akorát.",
    "Jídlo bylo dobře dochucené.",
    "Moc dobré, děkuji.",
    "Příloha i omáčka byly povedené.",
]

NEUTRAL_NOTES = [
    "",
    "",
    "Bylo to dobré, jen trochu méně výrazné.",
    "Porce byla v pořádku.",
    "Příště bych uvítal/a více zeleniny.",
]

WEAKER_NOTES = [
    "",
    "Jídlo bylo trochu málo teplé.",
    "Příště bych uvítal/a méně soli.",
    "Porce mohla být o něco větší.",
    "Chuťově průměrné.",
]


class Command(BaseCommand):
    help = "Doplní prezentační hodnocení jídel do ankety podle vydaných objednávek."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=900,
            help="Maximální počet doplněných hodnocení.",
        )
        parser.add_argument(
            "--coverage",
            type=int,
            default=70,
            help="Přibližné procento vydaných položek, které dostanou hodnocení.",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=20260420,
            help="Seed náhodného generátoru pro opakovatelné demo výsledky.",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Smaže existující hodnocení a vytvoří je znovu.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        rng = random.Random(options["seed"])
        limit = max(1, int(options["limit"]))
        coverage = min(100, max(1, int(options["coverage"])))

        if options["reset"]:
            deleted, _ = HodnoceniJidla.objects.all().delete()
            self.stdout.write(f"Smazáno existujících záznamů ankety: {deleted}")

        questions = self._ensure_questions()
        items = self._candidate_items(limit, coverage, rng)
        if not items:
            self.stdout.write(self.style.WARNING(
                "Nenašel jsem žádné vydané položky bez hodnocení. "
                "Nejprve vytvoř historii objednávek a výdejů, případně spusť command s --reset."
            ))
            return

        created = 0
        answer_count = 0
        for item in items:
            hodnoceni = HodnoceniJidla.objects.create(
                user=item.order.user,
                order_item=item,
                datum_vydeje=item.order.datum_vydeje,
                jidlo_nazev=item.menu_item.jidlo.nazev,
                poznamka=self._note_for_item(item, rng),
                vytvoreno=self._rating_datetime(item.order.datum_vydeje, rng),
            )
            for question, score in zip(questions, self._scores_for_item(item, rng, len(questions))):
                OdpovedHodnoceni.objects.create(
                    hodnoceni_jidla=hodnoceni,
                    otazka=question,
                    znamka=score,
                )
                answer_count += 1
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"Doplněno hodnocení jídel: {created}, odpovědí: {answer_count}."
        ))

    def _ensure_questions(self):
        questions = []
        for index, text in enumerate(DEFAULT_QUESTIONS, start=1):
            question, _ = AnketniOtazka.objects.update_or_create(
                text=text,
                defaults={
                    "napoveda": "Hodnocení 1 až 5, kde 5 je nejlepší.",
                    "aktivni": True,
                    "povinna": True,
                    "poradi": index,
                },
            )
            questions.append(question)
        return questions

    def _candidate_items(self, limit, coverage, rng):
        qs = (
            OrderItem.objects
            .filter(vydano=True, hodnoceni__isnull=True)
            .select_related("order__user", "menu_item__jidlo", "menu_item__druh_jidla")
            .order_by("-order__datum_vydeje", "order__user_id", "id")[: limit * 2]
        )
        selected = []
        for item in qs:
            if len(selected) >= limit:
                break
            if rng.randint(1, 100) <= coverage:
                selected.append(item)
        return selected

    def _base_score_for_item(self, item, rng):
        name = (item.menu_item.jidlo.nazev or "").lower()
        bonus = 0
        if any(word in name for word in ["řízek", "svíčková", "kuře", "rajská", "kaše", "buchtičky", "lasagne"]):
            bonus += 1
        if any(word in name for word in ["ryba", "luštěnin", "čočka", "jáhly", "špenát"]):
            bonus -= 1

        base = rng.choices([2, 3, 4, 5], weights=[5, 20, 48, 27], k=1)[0] + bonus
        return max(1, min(5, base))

    def _scores_for_item(self, item, rng, count):
        base = self._base_score_for_item(item, rng)
        scores = []
        for index in range(count):
            offset = rng.choice([-1, 0, 0, 0, 1])
            if index == 1 and base >= 4:
                offset = rng.choice([0, 0, 1])
            if index == 2:
                offset = rng.choice([-1, 0, 0, 1])
            scores.append(max(1, min(5, base + offset)))
        return scores

    def _note_for_item(self, item, rng):
        base = self._base_score_for_item(item, rng)
        if base >= 5:
            return rng.choice(POSITIVE_NOTES)
        if base >= 4:
            return rng.choice(POSITIVE_NOTES + NEUTRAL_NOTES)
        if base == 3:
            return rng.choice(NEUTRAL_NOTES)
        return rng.choice(WEAKER_NOTES)

    def _rating_datetime(self, day, rng):
        value = datetime.combine(day, time(hour=rng.randint(12, 19), minute=rng.randint(0, 59)))
        value += timedelta(seconds=rng.randint(0, 59))
        return timezone.make_aware(value)
