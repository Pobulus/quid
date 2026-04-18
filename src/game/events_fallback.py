"""Hand-written SAGE events. Used as the mock source for Phase 1 Track B and as
the safety-net fallback once the real LLM is wired up.

Event schema (matches the SAGE contract):

    {
        "slug": str,                           # stable id used for recent_events dedup
        "title": str,                          # inbox subject line
        "sender": str,                         # inbox "from" field
        "body": str,                           # markdown body
        "options": [
            {
                "id": "a" | "b" | "c" | ...,
                "label": str,
                "skill_check": {"skill": str, "difficulty_class": int} | None,
                "effects_on_success": {<effect_key>: int, ...},
                "effects_on_failure": {<effect_key>: int, ...},
                "hint": str,
            },
            ...
        ],
    }

Non-check options have `skill_check = None` and identical success/failure effect
dicts (one shape, not two — see CLAUDE.md).

Effect keys are the closed set in `balance.EFFECT_KEYS`. `money` is grosze.
"""

from __future__ import annotations


FALLBACK_EVENTS: list[dict] = [
    {
        "slug": "boiler_emergency",
        "title": "The boiler is making a noise again",
        "sender": "Landlord",
        "body": (
            "Woke up to a grinding noise from the hallway. The boiler is clearly "
            "dying. Your landlord says it's *\"basically working\"* and suggests "
            "you **take a look yourself** before he sends a plumber (who will bill "
            "you for the call-out)."
        ),
        "options": [
            {
                "id": "a",
                "label": "Fix it yourself",
                "skill_check": {"skill": "handiwork", "difficulty_class": 12},
                "effects_on_success": {"handiwork": 1, "sanity": -5},
                "effects_on_failure": {"money": -25000, "sanity": -10, "handiwork": 1},
                "hint": "Handiwork DC 12. Fail = plumber call-out fee.",
            },
            {
                "id": "b",
                "label": "Pay a plumber",
                "skill_check": None,
                "effects_on_success": {"money": -30000, "sanity": -2},
                "effects_on_failure": {"money": -30000, "sanity": -2},
                "hint": "Safe. Costs 300 PLN.",
            },
            {
                "id": "c",
                "label": "Ignore it",
                "skill_check": None,
                "effects_on_success": {"sanity": -8, "energy": -5},
                "effects_on_failure": {"sanity": -8, "energy": -5},
                "hint": "No cost now. Sleep will suffer.",
            },
        ],
    },
    {
        "slug": "overtime_offer",
        "title": "Overtime shift available this weekend",
        "sender": "Your manager",
        "body": (
            "Marta from work pings you: *\"Hey — we need someone to cover Saturday. "
            "Double pay. You in?\"* You could use the cash, but the week has already "
            "been brutal."
        ),
        "options": [
            {
                "id": "a",
                "label": "Take the shift",
                "skill_check": {"skill": "physique", "difficulty_class": 11},
                "effects_on_success": {"money": 40000, "energy": -20, "physique": 1},
                "effects_on_failure": {"money": 20000, "energy": -35, "health": -10},
                "hint": "Physique DC 11. Fail = half pay, you burn out.",
            },
            {
                "id": "b",
                "label": "Negotiate for remote work instead",
                "skill_check": {"skill": "charisma", "difficulty_class": 14},
                "effects_on_success": {"money": 40000, "energy": -10, "charisma": 1},
                "effects_on_failure": {"sanity": -8},
                "hint": "Charisma DC 14. Fail = awkward, no money.",
            },
            {
                "id": "c",
                "label": "Decline",
                "skill_check": None,
                "effects_on_success": {"sanity": 5},
                "effects_on_failure": {"sanity": 5},
                "hint": "Rest now. Keep your weekend.",
            },
        ],
    },
    {
        "slug": "friend_asks_loan",
        "title": "Kamil needs to borrow 200 PLN",
        "sender": "Kamil",
        "body": (
            "*\"Hey, awkward ask — can you spot me 200 until next Friday? Rent's "
            "late and I'm panicking. I'll pay you back, promise.\"*\n\n"
            "Kamil has flaked on you before. Once."
        ),
        "options": [
            {
                "id": "a",
                "label": "Lend it, trust him",
                "skill_check": {"skill": "charisma", "difficulty_class": 13},
                "effects_on_success": {"money": 0, "charisma": 1, "sanity": 3},
                "effects_on_failure": {"money": -20000, "sanity": -5},
                "hint": "Charisma DC 13 (reading him). Fail = he ghosts.",
            },
            {
                "id": "b",
                "label": "Say you're broke too",
                "skill_check": None,
                "effects_on_success": {"sanity": -3},
                "effects_on_failure": {"sanity": -3},
                "hint": "Keep the cash. Feels bad.",
            },
        ],
    },
    {
        "slug": "scam_phone_call",
        "title": "\"This is your bank's security department\"",
        "sender": "Unknown number",
        "body": (
            "A calm voice on the phone: *\"We've detected suspicious activity on "
            "your account. To secure it, please confirm your card number and "
            "CVV…\"* \n\nYour actual bank has never once called you."
        ),
        "options": [
            {
                "id": "a",
                "label": "Hang up immediately",
                "skill_check": None,
                "effects_on_success": {"sanity": 2},
                "effects_on_failure": {"sanity": 2},
                "hint": "Smart. Nothing happens.",
            },
            {
                "id": "b",
                "label": "Play along to waste their time",
                "skill_check": {"skill": "charisma", "difficulty_class": 15},
                "effects_on_success": {"charisma": 1, "sanity": 5},
                "effects_on_failure": {"money": -50000, "credit_score": -10, "sanity": -15},
                "hint": "Charisma DC 15. Fail = you actually leak info.",
            },
            {
                "id": "c",
                "label": "Give them the details (they sound legit)",
                "skill_check": None,
                "effects_on_success": {"money": -50000, "credit_score": -15, "sanity": -20},
                "effects_on_failure": {"money": -50000, "credit_score": -15, "sanity": -20},
                "hint": "Don't do this.",
            },
        ],
    },
]


def get_by_slug(slug: str) -> dict | None:
    for ev in FALLBACK_EVENTS:
        if ev["slug"] == slug:
            return ev
    return None
