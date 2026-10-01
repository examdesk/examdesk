import os

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "examdesk.settings")
application = get_wsgi_application()

if not settings.DEBUG and "DJANGO_SECRET_KEY" not in os.environ:
    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY: the default key is public.")
