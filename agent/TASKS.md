# TASKS.md — Financial Responsibility RPG

Single-player browser game. Django serves the shell and proxies LLM calls. All gameplay state lives in the browser’s `localStorage`. Ollama with Gemma runs on a Mac reachable over Tailscale.

-----

## Assumed defaults

If anything in this doc conflicts with a decision made later, the later decision wins. Defaults:

- **Currency:** PLN
- **Game length:** open-ended. The game continues indefinitely; it only ends when a player stat hits 0 (game over). The “~3 in-game month” figure below is a **demo arc target** — how much in-game time a judge should see to get the point — not a win condition.
- **Starting scenario:** fresh grad, first rented apartment, entry-level job
- **Day advancement:** manual, with an “advance until event” shortcut
- **Event frequency:** stress-scaled — baseline 30%, rises with poor stats / debt load, cap 70%
- **Work:** automatic on weekdays, player’s daily action slot is always free choice
- **Investments:** stubbed UI only, locked behind credit score 750
- **Team size:** 3 devs
- **Duration:** 24h continuous
- **LLM:** Gemma on Ollama, reachable via Tailscale. Claude Code must prompt the user for `OLLAMA_HOST` (e.g. `http://mac.tailnet.ts.net:11434`) and `OLLAMA_MODEL` (e.g. `gemma3:4b`) on first setup and write them to `.env`. Never hardcode.

-----

## Architecture at a glance

```
Browser (localStorage holds full GameState as JSON)
  │
  │ POST /api/action/...      ← request includes full state + action
  ▼
Django (stateless re: gameplay)
  ├─ rules engine: finance.py, events.py, balance.py  (pure functions)
  ├─ SAGE proxy: sage.py → Ollama (Gemma)
  └─ returns new state + any event payload
```

- **Server is stateless.** It receives `GameState`, applies rules or generates an event, returns new `GameState`. Treat incoming state as untrusted but don’t build anti-cheat — this is MVP.
- **Client owns the d20 roll** for skill checks. LLM only provides the DC.
- **SQLite exists** because Django wants it, but no gameplay data lives there. Optionally: event-generation logs for debugging.

-----

## Global conventions

Read this before touching any file.

- **Schema version.** `GameState` has `"schema_version": 1` at the top. On load, if version mismatches, show “incompatible save, start new game” and clear localStorage.
- **Effect keys are closed.** Events can only modify these stat keys: `health`, `hunger`, `sanity`, `energy`, `money`, `credit_score`, `cooking`, `handiwork`, `charisma`, `physique`. Nothing else. The LLM is constrained to this set in the prompt and the validator rejects anything else.
- **Option schema is uniform.** Every option has `skill_check: {skill, difficulty_class} | null`, `effects_on_success: {...}`, `effects_on_failure: {...}`. For non-check options, both effect objects are equal and `skill_check` is `null`. One shape, not two.
- **Balance numbers live in `balance.py` only.** No magic numbers elsewhere. If you need a number, add a named constant there and import it.
- **`base_success_probability` is not in the LLM contract.** It’s computed client-side from the skill’s value and the DC. The LLM never touches it.
- **Money is an integer in grosze** (1 PLN = 100 grosze) to avoid float drift. The UI divides by 100 for display.
- **Endpoints return `{state, ...}`** with the full new state. Client replaces localStorage wholesale.

-----

## Data model (GameState)

Everyone needs to agree on this shape before splitting off. Dataclass names, fields, and types are the contract.

```python
# schema_version: 1
GameState:
  schema_version: int               # 1
  seed: int                         # for client-side d20 rolls
  day: int                          # 1..28, resets each month (months are 28 days, day 1 = Monday)
  month: int                        # 1..N, starts at 1
  day_of_week: int                  # 0=Mon .. 6=Sun; each month begins on Monday
  actions_today: int                # 0 or 1

  player: Player
    stats: {health, hunger, sanity, energy}        # 0..100
    skills: {cooking, handiwork, charisma, physique}  # int levels, 0..10
    skill_practice_counts: {<skill>: int}          # resets to 0 on level up
    salary_gross_monthly: int                      # grosze
    tax_rate: float                                # e.g. 0.22
    workdays_this_month: int

  accounts: Accounts
    checking: int                                  # grosze
    savings: int                                   # grosze
    savings_goal: {name: str, target: int} | None

  credit_card: CreditCard | None
    limit: int
    balance: int
    apr: float                                     # annual, e.g. 0.24
    due_day: int
    min_payment_pct: float                         # e.g. 0.05

  loans: [Loan]
    kind: "personal" | "bnpl" | "payday"
    principal: int
    remaining: int
    apr: float
    monthly_payment: int
    due_day: int
    payments_made: int
    payments_missed: int

  credit_score: int                                # 300..850, also gates product unlocks
  house: House
    tier: "shoddy_rental" | "decent_rental" | "nice_rental"
    shoddiness: int                                # 0..10
    durability: int                                # 0..10
    distance_to_work_km: int
    monthly_rent: int

  calendar: [CalendarEvent]
    day: int
    month: int
    kind: "payday" | "rent_due" | "cc_due" | "loan_due" | "heating_bill"
    amount: int
    auto_resolve: bool                             # true = fires silently on day tick

  inbox: [EventRef]                                # unread/read SAGE events
    event_id: str
    received_day: int
    received_month: int
    status: "unread" | "resolved"
    event: Event                                   # full event payload cached here

  recent_events: [{slug: str, days_ago: int}]      # last 8, for SAGE prompt context

  flags: dict                                      # tutorial_seen, took_bnpl_this_month, etc.
  game_over: {cause: str, flavor: str} | None
```

**Starting values** (fresh grad):

- `checking: 120000` (1200 PLN), `savings: 0`
- `salary_gross_monthly: 450000` (4500 PLN), `tax_rate: 0.22`
- stats all 70, skills all 1
- `house: shoddy_rental`, rent 180000 (1800 PLN), shoddiness 6, durability 4
- `credit_score: 600`
- no credit card, no loans

-----

## Progression tiers (what unlocks when)

The game is open-ended. There’s no “you won” screen — only game-over. Long-term retention comes from the **unlock ladder**: the more financially competent the player becomes, the more tools they get access to. Two independent axes drive unlocks:

- **Credit score** (300–850) — gates credit products. Earned through on-time payments and healthy utilization.
- **Net worth** (checking + savings − debt) — gates capital-intensive products. Earned by not spending everything.

Some unlocks require **both** axes. Put the exact thresholds in `balance.py` as `UNLOCK_TIERS` so tuning is one-file.

### Tier table (seed values — tune in `balance.py`)

|Unlock                                        |Credit score|Net worth (PLN)|Notes                                     |
|----------------------------------------------|------------|---------------|------------------------------------------|
|Basic checking account                        |—           |—              |available from start                      |
|BNPL / payday loans                           |—           |—              |always available (predatory on purpose)   |
|Basic savings account                         |—           |—              |available from start, low interest        |
|Starter credit card (low limit, high APR)     |600         |—              |                                          |
|Personal loan (standard APR)                  |650         |—              |                                          |
|Premium savings account (higher interest)     |—           |5,000          |                                          |
|Better credit card (higher limit, lower APR)  |700         |3,000          |                                          |
|Fixed-term deposit (lockup, best savings rate)|—           |10,000         |                                          |
|Investment products (funds)                   |750         |20,000         |**currently stubbed — shows locked state**|
|Mortgage eligibility                          |750         |50,000         |out of MVP scope, visible as locked       |
|Move to decent rental (Home app)              |—           |3,000          |player-triggered                          |
|Move to nice rental                           |—           |15,000         |player-triggered                          |

### Behavioral unlocks (nice to have if time)

Triggered by `flags` that accumulate over time:

- **`cc_paid_in_full_3mo`** → Bank app offers upgrade to better card
- **`rent_never_missed_6mo`** → landlord offers lease renewal at 5% discount
- **`savings_goal_completed`** → unlocks second savings goal slot

If time is short, ship just the credit-score + net-worth tier table. Behavioral unlocks are gravy.

### Why this matters for the demo

In a 3-month demo arc, a judge should see:

- Start with basic banking only
- Around month 1–2: unlock a starter credit card (if they manage score well)
- Around month 2–3: hit the savings threshold for premium savings
- See the locked **investment** tier as a visible goal on the horizon (credit score 750 + 20k PLN)

The locked tier is important — it’s the visible proof that the game has more depth than the demo window shows.

-----

## Phase 0 — Shared foundation (1 person, ~2h, blocks everyone else)

-----

Pick **one dev** to own this. Nobody else starts real work until this lands.

### T0.1 — Scaffold

Django 5 project `finrpg`, single app `game`. django-ninja installed. SQLite. Tailwind + DaisyUI via CDN, HTMX + Alpine via CDN. Base template with empty phone mockup shell (dock with 4 icons, content area). `python manage.py runserver` works. `.env` file with `OLLAMA_HOST`, `OLLAMA_MODEL`, `DJANGO_SECRET_KEY`. README with setup steps.

**On first run, Claude Code must prompt the user for the Ollama URL over Tailscale and write it to `.env`.**

### T0.2 — Data model + balance.py

All dataclasses from the spec above, with `to_dict`/`from_dict`. Schema version constant. `balance.py` with every tunable number: starting state, tax rate, rent by house tier, APR bands by product, skill practice rates (base 20%, +10% per attempt, cap 80%, reset on level-up), stat decay per day, game-over thresholds (stat ≤ 0 triggers game over), event frequency formula (`0.30 + 0.05 * (debt_pressure + stat_deficit)` clamped to 0.70), credit score weights (60/30/10 for history/utilization/age).

### T0.3 — New-game endpoint + state round-trip

`POST /api/new-game` returns a fresh `GameState` JSON. `POST /api/echo` round-trips state through `to_dict`/`from_dict` to catch schema drift. Frontend: on boot, load from localStorage or call `/api/new-game`, render something trivial (e.g. display day/month/money). This is the “hello world” everyone else builds on.

**Definition of done for Phase 0:** page loads, shows fresh game state, state persists across reloads via localStorage, `curl` to `/api/new-game` returns valid JSON matching the schema.

-----

## Phase 1 — Parallel tracks (3 devs, ~12h)

Once Phase 0 is in main, split. Each track has clear inputs (the schema) and outputs (endpoints or components). Integration happens in Phase 2.

### 🟦 Track A — Finance + core loop (Dev A)

The beating heart. Pure Python, easily testable, no UI dependencies.

#### A1 — `finance.py` (pure functions, state → state)

- `pay_salary(state)` — gross × workdays ratio, minus tax, credited to checking
- `apply_monthly_interest(state)` — savings interest (+), loan interest (+ to balance), CC interest on carried balance
- `charge_rent(state)`, `charge_heating(state, month)` — seasonal heating (winter months 2–3× summer)
- `charge_credit_card_bill(state)` — minimum payment auto-debit if possible, otherwise flag missed payment
- `make_loan_payment(state, loan_id)` — explicit, called by calendar or player
- `take_loan(state, kind, amount)` — availability gated by credit_score
- `take_bnpl(state, amount)` — always available, 0% for 30 days then 40% APR
- `update_credit_score(state)` — recomputed monthly from payment history, utilization, account age
- `net_worth(state) -> int` — checking + savings − sum(loan.remaining) − cc.balance
- `available_products(state) -> list[str]` — checks both credit_score and net_worth against `UNLOCK_TIERS` in `balance.py`. Drives which UI elements are active and which appear as locked.

Every function is pure: takes state, returns new state + optional log message. No side effects.

#### A2 — Day tick + calendar engine

- `POST /api/advance-day` — advances one day, applies stat decay, fires calendar events whose `day/month` match, checks game-over
- `POST /api/advance-until-event` — loops advance-day until an event triggers, a calendar event fires needing attention, month boundary hit, or game over
- `POST /api/set-budget` — called at payday, player allocates categories (food, leisure, bills buffer). Locks in for the month.
- `POST /api/practice-skill` — rolls per `balance.py` rates, increments skill_practice_counts, levels up on success
- `POST /api/rest` — +sanity, +energy, consumes the action

Calendar events that are `auto_resolve: true` (rent, heating) fire silently and subtract money. Those that need decision (loan due when you can’t afford it) create an inbox entry.

#### A3 — Game-over detection

Check after every state mutation. If `health`, `hunger`, `sanity`, or `energy` ≤ 0, set `game_over` with cause-specific flavor text. Frontend shows modal.

**Endpoints owned by Track A:** `/api/new-game` (co-owned with Phase 0), `/api/advance-day`, `/api/advance-until-event`, `/api/set-budget`, `/api/practice-skill`, `/api/rest`.

**Test it via curl before Track C integrates.** A should have a working CLI demo: new game → advance 90 days → observe credit score move, rent paid, salary credited.

-----

### 🟨 Track B — SAGE (Dev B)

The AI-generated event system. Can be built in total isolation from UI. Track A’s schema is the only dependency.

#### B1 — Prompt builder

`sage.py:build_prompt(state) -> str`. System prompt describes game, tone (realistic, unforgiving, educational), the closed set of effect keys, the JSON schema with a full example. User prompt includes relevant state slice: stats, skills, money, credit_score, house tier, recent_events (with days_ago), current month (for seasonal flavor). Explicitly forbids prose, markdown fences, extra keys.

#### B2 — Ollama client

`sage.py:call_ollama(prompt) -> dict`. POSTs to `{OLLAMA_HOST}/api/chat` with `"format": "json"` and the configured model. 10s timeout. Returns parsed JSON or raises.

#### B3 — Validator

Pydantic model matching the event schema exactly. Rejects unknown effect keys. Rejects effect deltas outside sane bounds (money ±50000 grosze = ±500 PLN, stats ±30, credit_score ±25). One retry on validation failure with a “your previous response was invalid: {error}” message. On second failure, fall back.

#### B4 — Fallback templates

`events_fallback.py` with 10 hand-written events covering the space: boiler emergency, medical bill, overtime offer, friend asks for loan, scam phone call, grocery sale, power outage, surprise tax rebate, old friend visits, job interview opportunity. Each with 2–4 options, at least 3 with skill checks. Used when LLM fails twice or when Ollama is unreachable.

#### B5 — Event endpoint

`POST /api/sage/event` — receives state, decides whether an event should fire (per stress-scaled probability formula), returns `{event: Event} | {event: null}`. Appends event to `inbox` before returning.

#### B6 — Event resolution

`POST /api/event/resolve` — payload: `{event_id, option_id, roll_d20}`. Server looks up event in inbox, applies effects per roll vs DC, updates `recent_events`, marks inbox entry resolved, returns new state + `resolution: {rolled, dc, passed, effects_applied}`.

**Track B ships independent of UI.** Test via curl: new game → POST to `/api/sage/event` → get an event → POST to `/api/event/resolve` with a choice → see state change.

-----

### 🟩 Track C — Phone UI (Dev C)

Built on top of `/api/new-game` from Phase 0. Uses fake state initially (hardcoded JSON) for any endpoints not yet ready. Integrates with A and B in Phase 2.

#### C1 — Phone shell + dock

Phone-mockup chrome (rounded corners, dark frame, notch). Dock with 4 app icons (Email, Home, Bank, Health). Alpine store `appState = { activeApp: 'home', ... }`. HTMX not strictly needed if Alpine handles app switching — pick one and stick with it. **Recommend: Alpine for UI state, HTMX only for server-backed swaps.** Cyberpunk theme: neon accents, pixel font (Press Start 2P) for headers, VT323 for body. DaisyUI theme customized.

#### C2 — Bank app

Checking + savings balances, credit card card, active loans, credit score gauge (circular progress). Budget allocation modal at payday (slider sum = salary). Simple bar chart of month-over-month money (CSS bars, no chart lib). Buttons for “take loan” (gated by credit_score), “make extra payment”, “transfer to savings”.

**Render locked tiers visibly.** For each product not yet unlocked, show it greyed out with the unlock requirement (“Credit score 750 + 20,000 PLN net worth”). This is the game’s long-term roadmap made visible — don’t hide locked content.

#### C3 — Home app

Current house card with flavor text (hardcoded per tier), shoddiness/durability bars. Calendar list: next 10 days of scheduled events from `state.calendar`. “Advance day” and “Advance until event” buttons live here (or in the top bar).

#### C4 — Health app

Four stat bars (health, hunger, sanity, energy), four skill rows with level + progress-toward-next bar. “Rest” and “Practice skill” buttons (disabled if `actions_today == 0`).

#### C5 — Email app

Inbox list: sender, title, day/month, unread dot. Detail view: event body rendered as markdown (use a tiny JS markdown lib via CDN, or just `marked`), option cards below. Each option card shows: label, skill + DC if applicable, and computed success probability. Click → client rolls d20 locally (using `state.seed` + deterministic advance), POSTs to `/api/event/resolve`, shows resolution animation (pass/fail flash), updates state.

#### C6 — Game-over modal

Full-screen takeover when `state.game_over != null`. Cause-specific flavor, “new game” button that wipes localStorage.

**Track C uses fake state until integration.** Keep a `fake_state.json` in the repo for local dev.

-----

## Phase 2 — Integration (~3h)

All 3 devs together. This is where tracks collide.

### T2.1 — Wire Track C to real endpoints

Replace fake state with real `/api/new-game`, advance-day, set-budget, practice-skill, rest, sage/event, event/resolve. Every state-returning endpoint → update localStorage + re-render.

**Set Budget modal (Track C):** the current button is a stub (`templates/index.html:151`). Build a simple modal with a 3-tier food selector (cheap / normal / premium) and POST `{state, budget: {food_tier}}` to `/api/set-budget`. Display the monthly cost + per-tier stat deltas from a small `FOOD_TIERS` mirror in `quid.js` (keep in sync with `balance.FOOD_TIERS`). Other budget lines (leisure, bills_buffer) are cosmetic — pure int inputs, stored in flags, no gameplay effect.

### T2.2 — Seed calendar on new-game

Payday on day 28, rent on day 5, heating on day 10, CC due if CC exists. Heating scales: months 1, 11, 12 = 3×; months 2, 10 = 2×; months 5–9 = 0.5×. Rollover re-seeds the next month's calendar with the same pattern.

**Status:** done in Track A — `state.seed_month_calendar` + `events._rollover_month`.

### T2.3 — Event loop end-to-end

Click "advance until event" → server loops day ticks → probability check → if fires, SAGE endpoint → event lands in inbox → Email app shows unread → player opens, picks option → roll → resolve → state updates. Full loop green.

**Integration note (A+B):** Track A's `/api/advance-until-event` currently returns `{state, logs, reason}` and stops on any calendar event or month boundary. Track C already checks `data.event` for a toast. Phase-2 plan: when `reason == "max_days"` (no calendar event fired and stress roll succeeds), the endpoint internally calls Track B's SAGE logic, appends the event to `state.inbox`, and includes `event` in its response. Alternative: Track C makes a follow-up call to `/api/sage/event` after advance-until-event returns — simpler but chattier. Pick one during integration.

### T2.6 — Inbox entries for non-auto calendar events

**Owner:** Track A. Calendar events with `auto_resolve: false` (e.g. `loan_due` when the player can't cover it, see fixture `static/fake_state.json`) currently sit in the calendar and never surface. On day tick, if a non-auto event matches today, generate a synthetic inbox entry (`event_id = f"cal_{kind}_{month}_{day}"`, status unread, body + options built from a small template) and leave the calendar entry for next-day retry until resolved. Needed so players can act on loan-due warnings from the Email app instead of them being silent.

**Status:** MVP shipped on branch `t2_track_a` (PR #4) — informational-only inbox entry on miss (`options: []`). Full-spec interactive options tracked as T2.7.

### T2.7 — Interactive missed-loan inbox (T2.6 full-spec)

**Owner:** Track A. Replace the informational-only inbox entry from T2.6 MVP with a real player-actionable one. When `_fire_loan_due` records a miss, push a synthetic event with 4 options: **pay now** (retry `make_loan_payment`), **pay from savings** (shuffle from savings, then pay), **take a payday loan to cover** (`finance.take_loan("payday", …)` + pay), **let it slide** (credit_score −15, sanity −5). Route resolution via `events.resolve_calendar_event(state, event_id, option_id)` — dispatched from `/api/event/resolve` by `cal_*` event-id prefix. `event_id` pattern `cal_loan_due_{month}_{day}_{loan_index}` carries the loan index for the resolver; d20 is ignored for calendar events. No schema change. Option effect dicts are display-only previews; real side effects live in the resolver. Expand `game/tests.py` with one test per option branch. UI template unchanged (inbox already iterates `options`). Full plan: [`agent/reports/t2.7-interactive-loan-inbox.md`](reports/t2.7-interactive-loan-inbox.md).

**Status:** shipped on branch `t2_6_interactive` — 4-option synthetic event, `events.resolve_calendar_event` resolver, api.py dispatches `cal_*` to it. 6 new tests (`InteractiveLoanInboxTest`) cover every option branch and pay-now retry success. `manage.py test game.tests` → 9/9 green.

### T2.4 — Unlock tiers gate the UI

**Owner:** Track C. Bank app reads `available_products(state)` and renders each product as active, lockable (not yet met), or locked-visible (shown as preview with requirement). Both credit score and net worth factor in. Investment tab shows “unlocks at credit score 750 + 20,000 PLN net worth”.

### T2.5 — Practice skill rate progression

`skill_practice_counts` increments on attempt, resets on level-up. Verify the math matches `balance.py` (base 20%, +10%, cap 80%).

-----

## Phase 3 — Polish & safety net (~4h)

Parallelize freely. Priority top to bottom.

### T3.1 — Ollama connectivity check on server boot

If Ollama is unreachable, log warning, set a flag, and force fallback templates for the whole session. The demo should never crash because a Mac went to sleep.

### T3.2 — Save/export/import

**Owner:** Track C. Buttons: “New game” (confirm), “Export save” (download JSON), “Import save” (file picker + schema check).

### T3.3 — Demo script

**Owner:** Track A. Hardcoded seed + scripted opening scenario for the demo: starts with a tempting BNPL offer in the inbox so judges see the “unforgiving” nature within 30 seconds.

### T3.4 — Cyberpunk theme pass

**Owner:** Track C. DaisyUI custom theme: base `#0a0a14`, primary `#00ffc3`, secondary `#ff0080`, accent `#ffcc00`. Scanline overlay (CSS only). Subtle CRT flicker on the phone frame. Don’t spend more than an hour here.

### T3.5 — Bug bash

**Owner:** Track A. Play through 3 months start to finish. Fix anything that crashes. Accept jank that doesn’t crash.

### T3.6 — Remove slug from SAGE prompt — assign UUID server-side

**Owner:** Track B. The system prompt currently asks the LLM to generate a `slug` field. The LLM should not own event identity.

Fix in `sage.py`: remove `slug` from the prompt schema and the Pydantic `_Event` validator. After `validate_event` passes, assign `event_id = str(uuid.uuid4())` server-side before `push_to_inbox`. Update `recent_events` dedup to use a server-chosen key (e.g. first 5 words of title lowercased) instead of LLM slug. No frontend change — frontend already uses `event_id` for lookup.

### T3.7 — **[URGENT]** Lock resolved events (no re-roll)

**Owner:** Track C (UI). Once a player resolves an event (picks option → d20 fires → outcome shown), they can currently roll again — re-rolling changes the outcome retroactively.

Fix: in `quid.js`, after `lastResolution` is set for the open event, hide option cards and the roll button. Use `x-show="!lastResolution"` guard on the options section and `x-show="lastResolution"` on the resolution panel. No backend change required — UI lock is sufficient for MVP. Optionally, mark the `EventRef` in inbox as `status: "resolved"` so the lock survives page reload.

### T3.8a — Force budget selection: backend gate

**Owner:** Track A.

Currently the player can advance days without ever setting a budget. Budget must be mandatory at the start of each month (after payday fires on day 28, before day 1 of the next month can proceed).

In `advance_day`, before ticking the day, check: if `state.month > 1` (or a rollover just happened) and `state.flags.get("budget_set_month") != state.month`, return early with `reason = "budget_required"` and do NOT advance. Set `state.flags["budget_set_month"] = state.month` inside `/api/set-budget` after storing the budget. `advance_until_event` must also bail on the same condition.

### T3.8b — Force budget selection: auto-open modal

**Owner:** Track C. Depends on T3.8a.

When `/api/advance-day` or `/api/advance-until-event` returns `reason == "budget_required"`, open the budget modal automatically instead of showing a toast. Modal must not be dismissable without submitting.

### T3.9 — Budget actually deducts money and applies stat effects

**Owner:** Track A (backend).

Currently `/api/set-budget` only stores values in `flags` — no money is deducted and no stats change.

**Fix in `finance.py`:** Food is now charged **daily** (not monthly). `apply_daily_food(state)` is called from `events.advance_day` on every tick:

1. Resolve `food_tier` from `state.flags["budget"]` (default `"normal"`). Budget can be changed mid-month — the next day tick picks up the new tier.
2. Walk down tiers if checking can't cover today's cost. If nothing is affordable, apply cheap-tier stat penalties and skip the hunger restore (scraps day).
3. `FOOD_TIERS[tier]["cost"]` is now **grosze per day**; stat deltas (`health`, `sanity`, `energy`) apply every day, clamped 0..100. `daily_hunger` restores hunger the same day.
4. Aggregate today's charge into a per-tier line in `state.flags["monthly_expenses"]` via `_bump_food_expense` (one "Food (cheap)" / "Food (normal)" / "Food (premium)" line that grows). Switching tiers mid-month produces a second line.
5. Rent, heating, CC min payment, and loan payments also append to `monthly_expenses` via `_record_expense`. `_rollover_month` resets the list so each month shows its own breakdown.

The old one-shot monthly food charge (`apply_monthly_food`) and the `food_daily_hunger` flag are gone.

**Frontend:** Home app renders `state.flags["monthly_expenses"]` as an expense breakdown card. Budget modal tiles show daily cost + per-stat daily deltas.

### T3.10 — Fix payday amount in upcoming calendar display

**Owner:** Track C (UI).

Home app upcoming events list shows payday with amount 0. Root cause: `CalendarEvent.amount` for `payday` is seeded as 0 (salary isn’t known at seeding time — it’s computed at fire time by `finance.pay_salary`). The calendar entry can’t store a precomputed value because workdays aren’t known yet.

**Fix options (pick one):**

- **Option A (backend):** In `seed_month_calendar`, pass `salary_gross_monthly` and store an estimated gross as `amount`. UI labels it "~salary". Actual net may differ (workdays, tax) but ballpark is useful.
- **Option B (frontend, preferred):** In the upcoming events renderer in `quid.js`, when `ev.kind === "payday"`, compute and display the estimated net from state: `Math.round(state.player.salary_gross_monthly * (1 - state.player.tax_rate))` and label it "est. net salary". Amount field in CalendarEvent stays 0 — no backend change needed.

Use Option B. No schema change, no backend change.

### T3.11a — Test coverage: finance.py + events.py

**Owner:** Track A.

Gaps in `game/tests.py`:

- `finance.py`: `pay_salary` net-of-tax, `charge_rent` insufficient-funds path, `update_credit_score` direction (on-time payment vs miss), `net_worth` calculation.
- `events.py`: `_rollover_month` re-seeds loan_due + month counter; `advance_until_event` returns `"month_rollover"` reason at boundary.

Add `FinanceTest` and `EventsTest` classes. Each test method covers one named behaviour. Every public function in `finance.py` should have at least one passing + one failure-path test. `manage.py test game.tests` must stay green.

### T3.11b — Test coverage: sage.py

**Owner:** Track B.

Gaps in `game/tests.py`:

- `sage.py`: `validate_event` rejects unknown effect key; `validate_event` rejects delta out of bounds; `_validate_batch` assigns UUID event_id; `resolve_event` raises on already-resolved; `resolve_event` applies correct effects branch (pass vs fail).

Add a `SageTest` class. Every public function in `sage.py` should have at least one passing + one failure-path test. `manage.py test game.tests` must stay green.

### T3.12a — Apply for credit card: backend + endpoint

**Owner:** Track A.

Finance engine already has `available_products(state)` and a `CreditCard` dataclass; what's missing is the acquisition path.

New `finance.apply_for_credit_card(state, tier)` (tier = `"starter" | "better"`) — validates unlock via `available_products`, reads limit/APR from `balance.CC_STARTER_*` / `CC_BETTER_*`, assigns `due_day = CC_DUE_DAY`, `min_payment_pct = CC_MIN_PAYMENT_PCT`, and seeds the `cc_due` calendar event for the current month (T2.2 already seeds it on new-game *if* `has_cc` — reuse `seed_month_calendar`'s branch or append one directly). Raise `ValueError` if the player already has a CC.

Endpoint: `POST /api/apply-cc` with `{state, tier}` → returns `{state, message}`.

### T3.12b — Apply for credit card: UI

**Owner:** Track C. Depends on T3.12a.

Products-panel tiles for `cc_starter` / `cc_better` become clickable when `productStatus(key) === 'active'`. On click, `applyForCreditCard(tier)` in `quid.js` POSTs `/api/apply-cc`, replaces state, and surfaces the returned `message` as a toast. Confirm dialog before committing.

### T3.13a — Transfer checking ↔ savings: backend + endpoint

**Owner:** Track A.

New `finance.transfer(state, direction, amount)` where `direction = "to_savings" | "to_checking"`. Validates `amount > 0` and source has the funds (else `ValueError`). Moves integer grosze between `state.accounts.checking` and `state.accounts.savings`.

Endpoint: `POST /api/transfer` with `{state, direction, amount}` → `{state, message}`.

### T3.13b — Transfer checking ↔ savings: UI

**Owner:** Track C. Depends on T3.13a.

Replace the stub `@click="showToast('Transfer UI — Phase 2.')"` with a modal mirroring the budget-modal pattern: direction radio + PLN amount input (converted to grosze). Submit POSTs `/api/transfer` and replaces state.

### T3.14a — Take personal loan: endpoint

**Owner:** Track A.

`finance.take_loan("personal", amount)` already works — it seeds the `loan_due` calendar entry and credits checking. What's missing is the player-initiated endpoint.

`POST /api/take-loan` with `{state, kind, amount}` where `kind ∈ {"personal", "bnpl"}`. Validate against `available_products` (personal loan requires credit score 650; bnpl always available), cap `amount` at a sane per-loan ceiling (add `MAX_PERSONAL_LOAN` / `MAX_BNPL` to `balance.py`), return `{state, message}`.

### T3.14b — Take personal loan: UI

**Owner:** Track C. Depends on T3.14a.

Products panel `personal_loan` tile opens an amount modal when active; `bnpl` tile is always live. Keep it terse — one modal reused via a `loanKind` slot on the Alpine component. Submit POSTs `/api/take-loan`.

### T3.15 — Ollama HTTP client (B2)

**Owner:** Track B. `generate_event_via_llm(state, call_fn)` is fully wired in `sage.py` but `call_fn` has never been implemented — today the endpoint still uses the deterministic `generate_event` mock. Add a real `call_fn` that POSTs to `{OLLAMA_HOST}/api/chat` with `format: "json"`, the built system/user prompts, and the model from `OLLAMA_MODEL`. Respect `OLLAMA_AVAILABLE` (T3.1). Flip `/api/sage/event` and `advance-until-event` to use the LLM path when available, fall back to `generate_event` otherwise. Keep a 10s timeout; any transport error short-circuits to fallback (the existing pipeline already handles this if the exception leaks).

### T3.16 — Post-event return to Home, not Email

**Owner:** Track C.

After resolving a SAGE event (d20 rolls, outcome shown, player taps "Close"), the UI currently leaves the player in the Email app staring at the inbox. The natural continuation is to push forward in time, not to re-browse mail.

Fix in `quid.js`: when the player closes the resolution panel of an event that arrived via `advanceUntilEvent` (i.e. the "random event of the day" flow), switch `activeApp` to `home` instead of clearing `openEventId` back to the inbox list. Inbox-initiated opens (user clicked a stale unread from the Email list) should still return to the inbox — distinguish via a transient `openedFromAdvance: true` flag set in `advanceUntilEvent` and cleared on close. No backend change.

### T3.17 — Low-stat warning dot + modal

**Owner:** Track C.

Players currently have no peripheral signal that a stat is approaching game-over. Once any of `health`, `hunger`, `sanity`, `energy` drops below 40/100, surface two affordances:

1. An **orange notification dot** on the Health app dock button (same visual as the Email unread dot, different color). Dot is present while **any** stat is `< 40`.
2. A one-shot **warning toast/modal** the first time a given stat crosses the 40 threshold in the current run. Track "already warned" in `state.flags.stat_warnings_shown: {<stat>: true}` so the modal doesn't re-fire every day while the stat hovers. Reset the entry when the stat climbs back above 50 (hysteresis — avoids chatter from a stat bouncing around the threshold).

Thresholds live in `quid.js` for now. No schema change.

### T3.18 — Rest is passive on day-advance; add "Go to sleep" in Health

**Owner:** Track C + small Track A tweak.

Currently `/api/rest` is an explicit daily action the player must click to spend, which is unnatural — real life doesn't ask you to opt into sleep. Change the model:

- **Track A (`events.advance_day`):** on day tick, if the player did **not** spend their action slot (`actions_today == 1` going in), apply the rest effect passively (`+REST_SANITY`, `+REST_ENERGY`, clamped). Log it as "Slept — recovered sanity/energy." This supersedes the explicit rest-action flow for the common case.
- **Track C:** remove the "Rest" button from the Health app (it's now implicit). Replace it with a **"Go to sleep"** primary button inside the Health app that calls `/api/advance-day`. Keep the Home-app day-advance buttons for now (they stay as the main loop controls), but Health's button is the one that matches the fiction.

Keep `/api/rest` endpoint around for explicit mid-day naps if we ever want them — just unlink it from the UI for MVP. No schema change.

### T3.20 — Credit card extra / full payment

**Owner:** Track A (backend + endpoint) + Track C (UI).

Track C's C2 spec calls for a "make extra payment" button on the credit card card, but only `finance.charge_credit_card_bill` (the automatic minimum on due day) is wired today. Players can't voluntarily pay down the balance, which makes interest unavoidable and the `cc_paid_in_full_3mo` behavioral unlock unreachable.

**Backend:** Add `finance.pay_credit_card(state, amount)` (pure). Validates `state.credit_card is not None`, `amount > 0`, `amount <= state.credit_card.balance`, and `amount <= state.accounts.checking`; raises `ValueError` otherwise. Decrements both, increments `flags.cc_payments_made` **only** if `amount >= min_payment` at time of call (so extra partial payments don't game the credit-score history). Appends to `monthly_expenses` as "CC payment". Returns `(state, message)`.

Endpoint: `POST /api/cc-pay` with `{state, amount}` → `{state, message}`; maps `ValueError` → HTTP 400.

**UI:** In the Bank app's existing Credit card card (`templates/index.html`), add two buttons under the balance bar: "Pay balance in full" (amount = current balance, disabled if checking can't cover) and "Pay extra…" (opens a small PLN amount modal, mirrors the transfer-modal pattern). Both call `applyCreditCardPayment(amount)` in `quid.js`, which POSTs `/api/cc-pay`, replaces state, and toasts the message. No schema change.

Tests: one success path, one rejection each for no-CC / over-balance / over-checking / non-positive.

### T3.21 — House move: upgrade / downgrade

**Owner:** Track A (backend + endpoint) + Track C (UI).

`UNLOCK_TIERS` gates `move_decent_rental` and `move_nice_rental` by net worth, but there's no player-triggered move path — the Home app shows the current tier and nothing else. Players need to be able to **upgrade** (shoddy → decent → nice) when they hit the net-worth threshold, and **downgrade** (nice → decent → shoddy) when they can't keep paying rent on the current tier. Both directions cost money (deposit + moving fees — a real tax on churning).

**Balance:** add to `balance.py`:

```
MOVE_COSTS = {  # grosze
    "upgrade":   {"deposit": 2 * rent_of_new_tier, "moving_fee": 50000},   # ~500 PLN
    "downgrade": {"moving_fee": 30000},                                     # ~300 PLN, deposit refunded
}
```

Deposit is returned to savings when moving out (shoddy tier has no prior deposit on a fresh grad; track `flags.house_deposit_paid: int` so downgrades can refund correctly — 0 if never upgraded).

**Backend:** `finance.move_house(state, target_tier)` (pure). Validates target ∈ `HOUSE_TIERS`, differs from current tier, player has funds for the move, and — if upgrading — the new tier's unlock is in `available_products(state)`. Deducts moving fee + deposit (for upgrades) / deducts moving fee and refunds prior deposit to checking (for downgrades). Mutates `state.house` to the new tier's config and rewrites `monthly_rent` + `shoddiness` + `durability` + `distance_to_work_km` from `HOUSE_TIERS`. Appends to `monthly_expenses` as "Moving". Raises `ValueError` on any failure.

Endpoint: `POST /api/move-house` with `{state, target_tier}` → `{state, message}`; `ValueError` → HTTP 400.

**UI:** In the Home app's house card, add a "Move" button that opens a modal listing all three tiers with their rent, shoddiness/durability, distance, and the computed move cost (upgrade = deposit + fee; downgrade = fee, minus deposit refund). Disabled/greyed for the current tier and for upgrades whose unlock isn't met (show the requirement). Confirm dialog before committing.

Tests: upgrade success, downgrade success + refund, reject when unlock not met, reject when insufficient funds, reject same-tier move.

### T3.22 — Premium savings + fixed-term deposit: open/switch

**Owner:** Track A (backend + endpoint) + Track C (UI).

`UNLOCK_TIERS` exposes `savings_premium` (5k PLN net worth) and `deposit` (10k PLN net worth) but there's no way to actually opt into them. Today `apply_monthly_interest` auto-picks premium vs basic purely from the balance threshold — the player never "opens" a product. That hides the mechanic and blocks the fixed-term deposit entirely.

Change the model to an **explicit savings tier** the player selects, plus a separate fixed-term deposit bucket.

**Schema:** bump `schema_version` → 2 (drop any pre-v2 saves per the standing convention). Extend `Accounts`:

```
Accounts:
  checking: int
  savings: int
  savings_tier: "basic" | "premium"    # default "basic"
  deposit: {principal: int, opened_month: int, term_months: int} | None
```

**Backend:**

- `finance.set_savings_tier(state, tier)` — validates unlock via `available_products` (premium requires `savings_premium` unlocked). Zero-cost switch; effect kicks in on next monthly interest.
- `finance.open_deposit(state, amount, term_months)` — requires `deposit` unlocked, `state.accounts.deposit is None`, `term_months in {3, 6, 12}` (constants in `balance.py`), `amount <= state.accounts.savings`. Moves `amount` from savings into `accounts.deposit` and stamps `opened_month = state.month`.
- `finance.close_deposit(state)` — releases principal + accrued interest back to savings. If closed before term, apply early-withdrawal penalty (`DEPOSIT_EARLY_PENALTY_PCT`, in `balance.py`).
- `apply_monthly_interest` honors `savings_tier` (no more auto-switch) and accrues deposit interest at `DEPOSIT_MONTHLY_RATE` on `accounts.deposit.principal`.

Endpoints: `POST /api/savings-tier` `{state, tier}`, `POST /api/deposit/open` `{state, amount, term_months}`, `POST /api/deposit/close` `{state}`. `ValueError` → 400.

**UI:** Bank app — under Accounts, a "Savings tier: Basic / Premium" toggle (Premium disabled until unlocked, shows requirement when locked). A "Fixed-term deposit" card appears once `deposit` is unlocked: "Open deposit…" button opens a modal with amount input + term radio (3/6/12 months), "Close deposit" button when one exists, with early-withdrawal penalty shown in the confirm dialog if applicable.

Tests: tier switch (allowed / gated), open deposit (success / insufficient savings / already open / unlock not met), close deposit (at term → full interest, early → penalty applied), `apply_monthly_interest` honors explicit tier and accrues deposit.

### T3.19 — Mobile portrait: drop the phone chrome

**Owner:** Track C.

The `.phone` mockup frame is charming on desktop but wastes screen real-estate on actual phones. On narrow portrait viewports (`max-width: 640px` and `orientation: portrait`), render `.screen` directly filling the viewport and hide the `.phone` bezel entirely. Also hide `.dev-bar` on the same breakpoint — judges on mobile don't need export/import/fake-state buttons. Pure CSS change in `quid.css`; no JS. Test both iOS Safari and mobile Chrome viewport emulation before calling done.

-----

## Cut list (don’t build these)

- Investments beyond the locked stub
- House buy/sell, passive income from rentals
- AI-generated house flavor text (hardcode per tier)
- Music, mascot, animations beyond CSS transitions
- Login, accounts, multiplayer, leaderboards
- Per-skill check for cooking/handiwork/physique in the UI (the LLM can still reference them; client rolls against whichever skill the event names)
- Event dedup by slug (the `recent_events` in the prompt is enough)
- Event prefetching — generate on demand, accept ~2–5s latency

-----

## Dev assignments (suggested)

- **Dev A** — Phase 0 lead, then Track A (finance + loop). Most “if one person should know the whole system” role.
- **Dev B** — Track B (SAGE). Isolated, prompt-heavy, lots of Ollama debugging.
- **Dev C** — Track C (UI). Starts with fake state, most visual progress early, best candidate to demo.

During integration everyone works together. Phase 3 split (no shared tasks):
- **Track A:** T3.3 Demo script, T3.5 Bug bash, T3.8a Budget gate (backend), T3.9 Budget deducts money, T3.11a Tests (finance + events), T3.12a Apply-CC backend, T3.13a Transfer backend, T3.14a Take-loan endpoint.
- **Track B:** T3.1 Ollama boot check, T3.6 Drop slug / server-side event_id, T3.11b Tests (sage.py), T3.15 Ollama HTTP client (B2).
- **Track C:** T2.1 Set Budget modal, T2.4 Unlock-tier UI gating, T3.2 Save/export/import, T3.4 Cyberpunk theme, T3.7 **[URGENT]** Lock resolved events, T3.8b Auto-open budget modal, T3.10 Payday amount display, T3.12b Apply-CC UI, T3.13b Transfer modal, T3.14b Loan modal.

-----

## Risk log

- **Ollama latency over Tailscale.** Test early. If > 10s per event, switch to smaller model or prefetch one event at payday.
- **Gemma JSON compliance.** Small Gemma models sometimes ignore `format: json`. The retry + fallback chain must work before anyone relies on it.
- **Balance numbers making the game unplayable.** Playtest after Phase 2. Be willing to change salary / rent / APRs in `balance.py` without touching logic.
- **Schema drift.** If one dev changes `GameState` shape mid-stream, all three break. Any schema change = Slack ping + version bump + everyone reloads their fake state.
- **Scope creep on UI.** Cyberpunk theme is a one-hour budget, not a three-hour budget.

-----

## Definition of done (hackathon)

A judge can:

1. Open the page, click “new game”, see the phone UI
1. Open the Bank app and set a budget at payday
1. Click “advance until event”, get an AI-generated event in Email
1. Pick an option (ideally a skill check), see the d20 resolve, see stats/money update
1. Watch their credit score change over 3 in-game months
1. See locked tiers in the Bank app (investment, mortgage) as visible long-term goals
1. Continue playing indefinitely — the game doesn’t end on a timer. Game-over is a lose condition (stat hits 0), not a win or timeout.

That’s the bar. Everything else is gravy.
