import os

from django.conf import settings
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examdesk.settings")
application = get_asgi_application()

if not settings.DEBUG and "DJANGO_SECRET_KEY" not in os.environ:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY: the default key is public.")
