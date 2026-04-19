"""GameState dataclasses with to_dict / from_dict round-trip.

Serialization rule: dataclass fields map 1:1 to JSON keys. None means absent
sub-objects (no credit card, no savings goal, not game over). Money is always
an integer in grosze.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

from game import balance as B


# ---- Sub-objects ----------------------------------------------------------------


@dataclass
class Player:
    stats: dict[str, int]              # health/hunger/sanity/energy 0..100
    skills: dict[str, int]             # cooking/handiwork/charisma/physique 0..10
    skill_practice_counts: dict[str, int]
    salary_gross_monthly: int          # grosze
    tax_rate: float
    workdays_this_month: int


@dataclass
class SavingsGoal:
    name: str
    target: int                        # grosze


@dataclass
class Deposit:
    principal: int
    opened_month: int
    term_months: int


@dataclass
class Accounts:
    checking: int
    savings: int
    savings_goal: Optional[SavingsGoal] = None
    savings_tier: Literal["basic", "premium"] = "basic"
    deposit: Optional[Deposit] = None


@dataclass
class CreditCard:
    limit: int
    balance: int
    apr: float
    due_day: int
    min_payment_pct: float


@dataclass
class Loan:
    kind: Literal["personal", "bnpl", "payday"]
    principal: int
    remaining: int
    apr: float
    monthly_payment: int
    due_day: int
    payments_made: int
    payments_missed: int


@dataclass
class House:
    tier: Literal["shoddy_rental", "decent_rental", "nice_rental"]
    shoddiness: int
    durability: int
    distance_to_work_km: int
    monthly_rent: int


@dataclass
class CalendarEvent:
    day: int
    month: int
    kind: Literal["payday", "rent_due", "cc_due", "loan_due", "heating_bill"]
    amount: int
    auto_resolve: bool


@dataclass
class EventRef:
    event_id: str
    received_day: int
    received_month: int
    status: Literal["unread", "resolved"]
    event: dict                        # full SAGE event payload


@dataclass
class RecentEvent:
    slug: str
    days_ago: int


@dataclass
class GameOver:
    cause: str
    flavor: str


# ---- Root -----------------------------------------------------------------------


@dataclass
class GameState:
    schema_version: int
    seed: int
    day: int
    month: int
    day_of_week: int                   # 0=Mon .. 6=Sun
    actions_today: int

    player: Player
    accounts: Accounts
    credit_score: int
    house: House

    credit_card: Optional[CreditCard] = None
    loans: list[Loan] = field(default_factory=list)
    calendar: list[CalendarEvent] = field(default_factory=list)
    inbox: list[EventRef] = field(default_factory=list)
    recent_events: list[RecentEvent] = field(default_factory=list)
    flags: dict[str, Any] = field(default_factory=dict)
    game_over: Optional[GameOver] = None

    # ---- (de)serialization ------------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GameState":
        if data.get("schema_version") != B.SCHEMA_VERSION:
            raise ValueError(
                f"schema version mismatch: got {data.get('schema_version')}, "
                f"expected {B.SCHEMA_VERSION}"
            )

        accounts_raw = data["accounts"]
        sg_raw = accounts_raw.get("savings_goal")
        dep_raw = accounts_raw.get("deposit")
        accounts = Accounts(
            checking=accounts_raw["checking"],
            savings=accounts_raw["savings"],
            savings_goal=SavingsGoal(**sg_raw) if sg_raw else None,
            savings_tier=accounts_raw.get("savings_tier", "basic"),
            deposit=Deposit(**dep_raw) if dep_raw else None,
        )

        cc_raw = data.get("credit_card")
        credit_card = CreditCard(**cc_raw) if cc_raw else None

        go_raw = data.get("game_over")
        game_over = GameOver(**go_raw) if go_raw else None

        return cls(
            schema_version=data["schema_version"],
            seed=data["seed"],
            day=data["day"],
            month=data["month"],
            day_of_week=data["day_of_week"],
            actions_today=data["actions_today"],
            player=Player(**data["player"]),
            accounts=accounts,
            credit_score=data["credit_score"],
            house=House(**data["house"]),
            credit_card=credit_card,
            loans=[Loan(**l) for l in data.get("loans", [])],
            calendar=[CalendarEvent(**e) for e in data.get("calendar", [])],
            inbox=[EventRef(**r) for r in data.get("inbox", [])],
            recent_events=[RecentEvent(**r) for r in data.get("recent_events", [])],
            flags=data.get("flags", {}),
            game_over=game_over,
        )


# ---- Factory --------------------------------------------------------------------


def seed_month_calendar(month: int, rent: int, has_cc: bool) -> list[CalendarEvent]:
    """Seed the standard recurring events for a given month."""
    heating_mult = B.HEATING_MONTH_MULTIPLIER.get(month, 1.0)
    events = [
        CalendarEvent(day=B.PAYDAY_DAY, month=month, kind="payday", amount=0, auto_resolve=True),
        CalendarEvent(day=B.RENT_DUE_DAY, month=month, kind="rent_due", amount=rent, auto_resolve=True),
        CalendarEvent(
            day=B.HEATING_DUE_DAY,
            month=month,
            kind="heating_bill",
            amount=int(B.HEATING_BASE * heating_mult),
            auto_resolve=True,
        ),
    ]
    if has_cc:
        events.append(
            CalendarEvent(day=B.CC_DUE_DAY, month=month, kind="cc_due", amount=0, auto_resolve=True)
        )
    return events


def new_game(seed: Optional[int] = None, demo: bool = False) -> GameState:
    if demo:
        seed = B.DEMO_SEED
    elif seed is None:
        seed = random.randint(1, 2**31 - 1)

    house_cfg = B.HOUSE_TIERS["shoddy_rental"]
    rent = house_cfg["rent"]
    state = GameState(
        schema_version=B.SCHEMA_VERSION,
        seed=seed,
        day=1,
        month=1,
        day_of_week=0,
        actions_today=1,
        player=Player(
            stats={k: B.STARTING_STAT for k in B.STAT_KEYS},
            skills={k: B.STARTING_SKILL for k in B.SKILL_KEYS},
            skill_practice_counts={k: 0 for k in B.SKILL_KEYS},
            salary_gross_monthly=B.STARTING_SALARY_GROSS,
            tax_rate=B.TAX_RATE,
            workdays_this_month=0,
        ),
        accounts=Accounts(
            checking=B.STARTING_CHECKING,
            savings=B.STARTING_SAVINGS,
        ),
        credit_score=B.STARTING_CREDIT_SCORE,
        house=House(
            tier="shoddy_rental",
            shoddiness=house_cfg["shoddiness"],
            durability=house_cfg["durability"],
            distance_to_work_km=house_cfg["distance_to_work_km"],
            monthly_rent=rent,
        ),
        calendar=seed_month_calendar(1, rent, has_cc=False),
        flags={
            "budget": {"food_tier": B.FOOD_DEFAULT_TIER},
        },
    )

    if demo:
        from game.demo_script import opening_bnpl_event
        from game.sage import push_to_inbox
        event = opening_bnpl_event()
        event["event_id"] = uuid.uuid4().hex
        push_to_inbox(state, event)

    return state
