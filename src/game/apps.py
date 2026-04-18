import os

import requests
from django.apps import AppConfig


class GameConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'game'

    def ready(self):
        from game import sage
        host = os.getenv("OLLAMA_HOST", "").strip()
        if not host:
            sage.OLLAMA_AVAILABLE = False
            print("[SAGE] OLLAMA_HOST not set — using fallback templates")
            return
        try:
            requests.get(host, timeout=2)
        except Exception:
            sage.OLLAMA_AVAILABLE = False
            print(f"[SAGE] Ollama unreachable at {host} — using fallback templates")
