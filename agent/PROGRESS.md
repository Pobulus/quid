# PROGRESS

One line per completed task from `TASKS.md`. Append at the bottom. Optional `[report](reports/<slug>.md)` link for tasks worth a write-up.

- 2026-04-18 · T0.1 Django scaffold: `finrpg` project + `game` app, django-ninja, Tailwind/DaisyUI/HTMX/Alpine via CDN, phone-mockup shell, `.env` wired.
- 2026-04-18 · T0.2 GameState dataclasses with to_dict/from_dict + schema_version; `balance.py` with starting state, APR bands, heating multipliers, `EFFECT_KEYS`, `EFFECT_DELTA_BOUNDS`, `UNLOCK_TIERS`.
- 2026-04-18 · T0.3 `POST /api/new-game` and `POST /api/echo` round-trip; frontend boots from localStorage or fetches new game; verified via curl.
- 2026-04-18 · C1 Phone shell refactor: split `static/css/quid.css` + `static/js/quid.js`; Alpine `activeApp` store; 4-app dock with active highlight + unread badge; status bar + toast + dev bar.
- 2026-04-18 · C2 Bank app: credit score gauge (conic-gradient), accounts + savings goal bar, credit card card, loans list, products panel with locked-visible tiles reading `UNLOCK_TIERS` (client mirror of `balance.py`) and showing requirement strings.
- 2026-04-18 · C3 Home app: house card (tier/rent/flavor/shoddiness/durability), advance-day + advance-until-event buttons wired to endpoints with graceful 404 toast, 10-day upcoming calendar.
- 2026-04-18 · C4 Health app: stat bars (colored by tier), skill rows with level + practice counter + per-skill Practice button (gated by `actions_today`), Rest action.
- 2026-04-18 · C5 Email app: inbox list + detail view, `tinyMarkdown` body renderer, option cards with skill/DC + client-side success probability, deterministic d20 via LCG on `state.seed`, POST to `/api/event/resolve` with local-fallback resolution display when endpoint not yet wired.
- 2026-04-18 · C6 Game-over modal: overlay shows when `state.game_over != null`, cause + flavor + new-game button wiping localStorage.
- 2026-04-18 · Fake-state fixture `src/static/fake_state.json` (CC + bnpl loan + calendar + boiler-emergency event); dev bar exposes "load fake" / "new game" / "export"; fixture round-trips cleanly through `/api/echo`.
- 2026-04-18 · B4+B5+B6 SAGE mock: 4 hand-written events in `events_fallback.py`, `sage.py` random picker (no Ollama), `/api/sage/event` and `/api/event/resolve` wired with d20 + clamped effects + recent_events update.
- 2026-04-18 · Frontend wired to SAGE mock: Home "Advance until event" calls `/api/sage/event` directly (Track A passthrough TBD), auto-jumps to Email + opens new event; Email empty-state has "Summon event now" CTA.
