from typing import Any

from django.core.management.base import BaseCommand

from examdesk import similarity


class Command(BaseCommand):
    help = "Download the sentence-embedding model that finds duplicate clarifications."

    def handle(self, *args: Any, **options: Any) -> None:
        for path in similarity.download():
            self.stdout.write(path)
