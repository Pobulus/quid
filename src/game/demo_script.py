"""Hand-written scripted events for the demo opener.

Kept out of `events_fallback.FALLBACK_EVENTS` so it never appears in
randomized play — only the demo-mode new-game seeds it into the inbox.
"""

from __future__ import annotations


def opening_bnpl_event() -> dict:
    return {
        "slug": "demo_opening_bnpl",
        "title": "Split it into 4 and pay nothing today",
        "sender": "PayLater+ <offers@paylater.pl>",
        "body": (
            "**0% for 30 days.** A 60-inch OLED for just 4× 750 PLN — "
            "no credit check, instant approval.\n\n"
            "*Miss a payment and the 40% APR kicks in retroactively.*"
        ),
        "options": [
            {
                "id": "a",
                "label": "Sign up, take the TV",
                "skill_check": None,
                "effects_on_success": {"sanity": 5},
                "effects_on_failure": {"sanity": 5},
            },
            {
                "id": "b",
                "label": "Haggle a smaller plan",
                "skill_check": {"skill": "charisma", "difficulty_class": 12},
                "effects_on_success": {"charisma": 1, "sanity": 2},
                "effects_on_failure": {"sanity": -3},
            },
            {
                "id": "c",
                "label": "Close the tab",
                "skill_check": None,
                "effects_on_success": {},
                "effects_on_failure": {},
            },
        ],
    }
