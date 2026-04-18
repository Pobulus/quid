# PROGRESS

One line per completed task from `TASKS.md`. Append at the bottom. Optional `[report](reports/<slug>.md)` link for tasks worth a write-up.

- 2026-04-18 · T0.1 Django scaffold: `finrpg` project + `game` app, django-ninja, Tailwind/DaisyUI/HTMX/Alpine via CDN, phone-mockup shell, `.env` wired.
- 2026-04-18 · T0.2 GameState dataclasses with to_dict/from_dict + schema_version; `balance.py` with starting state, APR bands, heating multipliers, `EFFECT_KEYS`, `EFFECT_DELTA_BOUNDS`, `UNLOCK_TIERS`.
- 2026-04-18 · T0.3 `POST /api/new-game` and `POST /api/echo` round-trip; frontend boots from localStorage or fetches new game; verified via curl.
- 2026-04-18 · B4+B5+B6 SAGE mock: 4 hand-written events in `events_fallback.py`, `sage.py` random picker (no Ollama), `/api/sage/event` and `/api/event/resolve` wired with d20 + clamped effects + recent_events update.
