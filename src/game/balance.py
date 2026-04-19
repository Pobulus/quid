"""All tunable numbers live here. No magic numbers anywhere else.

Money is in grosze (1 PLN = 100 grosze).
"""

from __future__ import annotations

SCHEMA_VERSION = 2

# Demo mode: fixed seed so the opening run is deterministic for judges.
DEMO_SEED = 13370420

# Calendar: months are 28 days, day 1 is Monday.
MONTH_LEN = 28
WORKDAYS_PER_MONTH = 20  # 4 weeks × 5 weekdays

# Seeded calendar events (relative to each month).
PAYDAY_DAY = 28  # last day of month — pays for the work just completed
RENT_DUE_DAY = 5
HEATING_DUE_DAY = 10

# Stat / skill bounds
STAT_MAX = 100
SKILL_MAX = 10
GAME_OVER_STAT = 0  # any stat <= this triggers game over

# Closed set of effect keys the LLM may touch.
EFFECT_KEYS = (
    "health", "hunger", "sanity", "energy",
    "money", "credit_score",
    "cooking", "handiwork", "charisma", "physique",
)

STAT_KEYS = ("health", "hunger", "sanity", "energy")
SKILL_KEYS = ("cooking", "handiwork", "charisma", "physique")

# Daily passive decay (applied each day tick before events).
STAT_DECAY_PER_DAY = {
    "health": 0,
    "hunger": 4,
    "sanity": 2,
    "energy": 3,
}

# Skill practice: base 20%, +10% per attempt at the same level, cap 80%, reset on level-up.
SKILL_PRACTICE_BASE = 0.20
SKILL_PRACTICE_STEP = 0.10
SKILL_PRACTICE_CAP = 0.80

# Rest action effect.
REST_SANITY = 12
REST_ENERGY = 25

# Event frequency (stress-scaled): 0.30 + 0.05 * (debt_pressure + stat_deficit), cap 0.70.
EVENT_BASE_PROB = 0.30
EVENT_PRESSURE_COEFF = 0.05
EVENT_PROB_CAP = 0.70

# Bounds the SAGE validator applies to LLM-supplied effect deltas.
EFFECT_DELTA_BOUNDS = {
    "money": (-50000, 50000),       # ±500 PLN
    "credit_score": (-25, 25),
    "health": (-30, 30),
    "hunger": (-30, 30),
    "sanity": (-30, 30),
    "energy": (-30, 30),
    "cooking": (-2, 2),
    "handiwork": (-2, 2),
    "charisma": (-2, 2),
    "physique": (-2, 2),
}

# ---- Starting state (fresh grad) ------------------------------------------------

STARTING_CHECKING = 300000        # 1200 PLN
STARTING_SAVINGS = 0
STARTING_SALARY_GROSS = 450000    # 4500 PLN / month
TAX_RATE = 0.22
STARTING_STAT = 70
STARTING_SKILL = 1
STARTING_CREDIT_SCORE = 600

# ---- Food -----------------------------------------------------------------------

# Three tiers of food spend. `cost` is grosze per DAY.
# `daily_hunger` = how much hunger is restored each day tick (offsets decay).
# Stat deltas apply every day tick (clamped to 0..100). Budget can change
# mid-month — the current tier is resolved on each day tick from
# `flags.budget.food_tier`, falling back to the cheapest affordable tier if
# checking can't cover today's cost.
FOOD_TIERS = {
    "cheap":   {"cost": 1100, "daily_hunger": 3, "health": -1, "sanity": -1, "energy":  0},
    "normal":  {"cost": 2150, "daily_hunger": 4, "health":  0, "sanity":  0, "energy":  1},
    "premium": {"cost": 4300, "daily_hunger": 5, "health":  1, "sanity":  1, "energy":  1},
}
FOOD_TIER_ORDER = ("cheap", "normal", "premium")
FOOD_DEFAULT_TIER = "normal"
COOKING_SHIFT_DIVISOR = 4  # +1 effective tier per 4 cooking levels

# ---- Houses ---------------------------------------------------------------------

HOUSE_TIERS = {
    "shoddy_rental": {
        "rent": 180000,
        "shoddiness": 6,
        "durability": 4,
        "distance_to_work_km": 12,
    },
    "decent_rental": {
        "rent": 260000,
        "shoddiness": 3,
        "durability": 6,
        "distance_to_work_km": 6,
    },
    "nice_rental": {
        "rent": 380000,
        "shoddiness": 1,
        "durability": 8,
        "distance_to_work_km": 3,
    },
}

HOUSE_TIER_ORDER = ["shoddy_rental", "decent_rental", "nice_rental"]

# Moving costs (T3.21). Upgrades cost a deposit (2× new-tier rent) + moving fee;
# downgrades refund the prior deposit but charge a smaller moving fee.
MOVE_UPGRADE_FEE = 50000     # 500 PLN
MOVE_DOWNGRADE_FEE = 30000   # 300 PLN
DEPOSIT_RENT_MULTIPLIER = 2  # deposit = 2× new-tier monthly rent

# Heating: months 1, 11, 12 = 3x; months 2, 10 = 2x; months 5–9 = 0.5x; rest 1x.
HEATING_BASE = 25000  # 250 PLN
HEATING_MONTH_MULTIPLIER = {
    1: 3.0, 2: 2.0, 3: 1.0, 4: 1.0,
    5: 0.5, 6: 0.5, 7: 0.5, 8: 0.5, 9: 0.5,
    10: 2.0, 11: 3.0, 12: 3.0,
}

# ---- Credit products ------------------------------------------------------------

# APR bands per product (annual, decimal). Tightened at higher score later if we add bands.
CC_STARTER_LIMIT = 100000      # 1000 PLN
CC_STARTER_APR = 0.34
CC_BETTER_LIMIT = 500000       # 5000 PLN
CC_BETTER_APR = 0.18
CC_MIN_PAYMENT_PCT = 0.05
CC_DUE_DAY = 25

PERSONAL_LOAN_APR = 0.14
MAX_PERSONAL_LOAN = 2000000    # 20000 PLN
BNPL_GRACE_DAYS = 30
BNPL_POST_GRACE_APR = 0.40
MAX_BNPL = 300000              # 3000 PLN
PAYDAY_LOAN_APR = 1.20         # brutal on purpose

# Credit score recompute weights (must sum to 1.0).
CREDIT_SCORE_WEIGHTS = {
    "history": 0.60,
    "utilization": 0.30,
    "age": 0.10,
}
CREDIT_SCORE_MIN = 300
CREDIT_SCORE_MAX = 850

# Savings interest (monthly, applied at month tick).
SAVINGS_BASIC_MONTHLY_RATE = 0.001    # ~1.2% APR
SAVINGS_PREMIUM_MONTHLY_RATE = 0.004  # ~5% APR
DEPOSIT_MONTHLY_RATE = 0.006          # ~7.4% APR (locked)
DEPOSIT_TERMS = (3, 6, 12)            # allowed term lengths in months
DEPOSIT_EARLY_PENALTY_PCT = 0.02      # 2% of principal forfeited on early close
SAVINGS_TIERS = ("basic", "premium")

# ---- Unlock tiers ---------------------------------------------------------------
# Each entry: (min_credit_score | None, min_net_worth_grosze | None)

UNLOCK_TIERS = {
    "checking_basic":     (None, None),
    "savings_basic":      (None, None),
    "bnpl":               (None, None),
    "payday_loan":        (None, None),
    "cc_starter":         (600, None),
    "personal_loan":      (650, None),
    "savings_premium":    (None, 500000),    # 5000 PLN
    "cc_better":          (700, 300000),     # 3000 PLN
    "deposit":            (None, 1000000),   # 10000 PLN
    "investments":        (750, 2000000),    # 20000 PLN — stubbed/locked in MVP
    "mortgage":           (750, 5000000),    # 50000 PLN — out of MVP, visible
    "move_decent_rental": (None, 300000),    # 3000 PLN
    "move_nice_rental":   (None, 1500000),   # 15000 PLN
}
