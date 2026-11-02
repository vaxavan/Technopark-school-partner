from django.core.management.base import BaseCommand

from partners import container


class Command(BaseCommand):
    help = "Обойти ресурсы школ сейчас и обновить статусы размещений"

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, default=None, help="ID организации")

    def handle(self, *args, **opts):
        report = container.detect_placement()(opts["org"])
        self.stdout.write(f"Проверено: {report.checked}, подтверждено: {report.confirmed}, "
                          f"требует решения: {report.disputed}, не найдено: {report.not_found}")
        for school, url, err in report.source_errors:
            self.stdout.write(self.style.WARNING(f"  {school}: {url} — {err}"))
