from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import override_settings
from django.test.utils import setup_test_environment

from examdesk import demos
from examdesk.views.exams import DEMOS


class Command(BaseCommand):
    help = "Render the landing page's demos from the app's pages, in an in-memory SQLite database."

    def handle(self, *args: Any, **options: Any) -> None:
        if connection.vendor != "sqlite" or connection.settings_dict["NAME"] != ":memory:":
            raise CommandError("Run with DATABASE_URL=sqlite://:memory:")
        no_migrations = {app.label: None for app in apps.get_app_configs()}
        with override_settings(MIGRATION_MODULES=no_migrations):
            call_command("migrate", run_syncdb=True, verbosity=0)
        setup_test_environment()  # for the test client that renders the pages
        demos.save(DEMOS)
        self.stdout.write(str(DEMOS))
