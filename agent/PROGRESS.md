# PROGRESS

One line per completed task from `TASKS.md`. Append at the bottom. Optional `[report](reports/<slug>.md)` link for tasks worth a write-up.

- 2026-04-18 · T0.1 Django scaffold: `finrpg` project + `game` app, django-ninja, Tailwind/DaisyUI/HTMX/Alpine via CDN, phone-mockup shell, `.env` wired.
- 2026-04-18 · T0.2 GameState dataclasses with to_dict/from_dict + schema_version; `balance.py` with starting state, APR bands, heating multipliers, `EFFECT_KEYS`, `EFFECT_DELTA_BOUNDS`, `UNLOCK_TIERS`.
- 2026-04-18 · T0.3 `POST /api/new-game` and `POST /api/echo` round-trip; frontend boots from localStorage or fetches new game; verified via curl.
- 2026-04-18 · Spec: months are 28 days, day 1 = Monday (updated `agent/TASKS.md`). Payday moved to day 28 so it pays for work already completed.
- 2026-04-18 · T1.A1 `finance.py` pure functions: pay_salary, charge_rent/heating, apply_monthly_interest, CC bill, loan payments, take_loan/bnpl, update_credit_score, net_worth, available_products.
- 2026-04-18 · T1.A2 `events.py` day-tick engine + calendar firing + month rollover; endpoints `/api/advance-day`, `/api/advance-until-event`, `/api/set-budget` (flags-only), `/api/practice-skill`, `/api/rest`. New-game seeds month-1 calendar (payday d28, rent d5, heating d10). Weekday tick increments `workdays_this_month`.
- 2026-04-18 · T1.A3 Game-over detection on any stat ≤ 0 after every tick, with cause-specific flavor text. Verified via curl: 3-month sim dies of hunger at day 19 (expected — food events belong to Track B).
- 2026-04-18 · Food quality: 3 tiers (cheap/normal/premium) with monthly cost + one-shot health/sanity/energy delta + daily hunger drip. `cooking // 4` shifts effective tier upward. Walks down to cheapest affordable if broke; "can't afford food" path applies cheap penalties with zero drip. Lives in `flags` (no schema bump).
