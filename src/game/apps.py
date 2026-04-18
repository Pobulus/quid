import requests
from django.apps import AppConfig
from django.conf import settings


class GameConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'game'

    def ready(self):
        from game import sage
        host = settings.OLLAMA_HOST
        try:
            requests.get(host, timeout=2)
            print(f"[SAGE] Ollama reachable at {host} (model={settings.OLLAMA_MODEL})")
        except Exception:
            sage.OLLAMA_AVAILABLE = False
            print(f"[SAGE] Ollama unreachable at {host} — using fallback templates")
