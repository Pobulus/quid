# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

QUID (Quest for Understanding Income & Debt) — a deliberately unforgiving financial-literacy RPG. Single-player, browser-based, built as a 24h 3-dev hackathon project. Currency is PLN.

The authoritative planning docs are:

- `docs/Hackathon_project.md` — feature spec, UI vision, SAGE LLM contract.
- `agent/TASKS.md` — build plan: phases, tracks, dev assignments, conventions, risk log. **Read this before making non-trivial changes.** Later decisions in that doc override earlier ones.
- `agent/PROGRESS.md` — running log of what's done (currently empty).

`src/` is empty — nothing has been implemented yet. Phase 0 in `TASKS.md` must land before parallel work starts.

## Stack & topology

- **Backend:** Django 5 + django-ninja, SQLite (only because Django requires it — **no gameplay data lives in the DB**).
- **Frontend:** Tailwind + DaisyUI via CDN, HTMX + Alpine via CDN, phone-mockup shell with a 4-app dock (Email, Home, Bank, Health).
- **LLM:** Ollama running Gemma on a Mac reachable over Tailscale. Configured via `OLLAMA_HOST` and `OLLAMA_MODEL` in `.env` — prompt the user for these on first setup, never hardcode.
- **Persistence:** the full `GameState` lives in the browser's `localStorage`. The Django server is **stateless with respect to gameplay** — every request sends the full state, the server returns the new state, client replaces localStorage wholesale. Treat incoming state as untrusted but don't build anti-cheat (MVP).

## Load-bearing conventions

Violating any of these silently breaks other tracks. They come from `agent/TASKS.md` § "Global conventions":

- **Money is an integer in grosze** (1 PLN = 100 grosze). No floats for money anywhere. UI divides by 100 for display only.
- **Effect keys are closed.** Events may only touch: `health`, `hunger`, `sanity`, `energy`, `money`, `credit_score`, `cooking`, `handiwork`, `charisma`, `physique`. The SAGE prompt constrains the LLM to this set and the validator rejects anything else.
- **Uniform option schema.** Every event option has `skill_check: {skill, difficulty_class} | null`, `effects_on_success`, `effects_on_failure`. For non-check options, both effect objects are equal and `skill_check` is `null`. One shape, not two.
- **Client owns the d20 roll.** The LLM only returns a difficulty class. `base_success_probability` is computed client-side from skill value vs DC — it is **not** in the LLM contract.
- **All balance numbers live in `balance.py`.** No magic numbers elsewhere in the codebase. If you need a tunable, add a named constant there and import it. This includes `UNLOCK_TIERS` (credit_score + net_worth thresholds that gate products).
- **`GameState` has `schema_version: 1`.** On load, a version mismatch means show "incompatible save, start new game" and clear localStorage. Any schema change = version bump and everyone reloads their fake state.
- **Endpoints return `{state, ...}`** with the full new state. Clients replace localStorage wholesale — don't expect patch semantics.
- **Rules engine functions are pure.** `finance.py` / `events.py` / `balance.py` take state, return new state (+ optional log message). No side effects, no I/O.

## Architectural shape

```
Browser (localStorage = full GameState JSON)
  │  POST /api/... with full state + action
  ▼
Django (stateless re: gameplay)
  ├─ rules engine: finance.py, events.py, balance.py   (pure functions)
  ├─ SAGE proxy:   sage.py → Ollama (Gemma, format=json)
  └─ returns new state + optional event payload
```

The SAGE proxy has a required retry + fallback chain: validator rejects → one retry with error message → second failure falls back to a hand-written template from `events_fallback.py`. If Ollama is unreachable at server boot, force fallback templates for the whole session. The demo must never crash because a Mac went to sleep.

## Endpoint map (planned)

Track A owns: `/api/new-game`, `/api/advance-day`, `/api/advance-until-event`, `/api/set-budget`, `/api/practice-skill`, `/api/rest`, `/api/echo` (state round-trip sanity check).

Track B owns: `/api/sage/event` (stress-scaled probability decides whether one fires), `/api/event/resolve` (payload includes the client's d20 roll).

## What's intentionally out of scope

See "Cut list" in `agent/TASKS.md` before building anything adjacent: investments beyond a locked stub, house buy/sell, AI-generated house flavor text (hardcode per tier), login/accounts, event prefetching, event dedup by slug. Don't add them without asking.

## Commands

Nothing is wired up yet. Once Phase 0 lands, expect the standard Django flow: `python manage.py runserver` for dev, and ad-hoc `curl` against the endpoints as the integration-test substitute (TASKS.md explicitly calls for `curl` smoke tests before UI integration).
