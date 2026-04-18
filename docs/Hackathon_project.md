# Financial Responsibility Educational RPG Game

Title: QUID — Quest for Understanding Income & Debt

A single-player, browser-based RPG in which the player lives month-to-month, makes financial decisions, and reacts to AI-generated life events. The goal is to teach financial literacy through a game that is deliberately unforgiving.

---

## Tech stack

- **Backend:** Django — serves the game shell and proxies prompts to the LLM.
- **Frontend styling:** DaisyUI (on Tailwind).
- **Persistence:** No database. The full game state lives in the browser's `localStorage`; the server is stateless with respect to gameplay.
- **LLM integration:** The server receives the current game state from the client, builds the SAGE prompt, calls the LLM, validates the response, and returns a structured event to the client.

> Note: Since state lives client-side, the server should treat incoming state as untrusted. For a hackathon MVP this is acceptable; flagging it as a known gap for anything beyond MVP.

---

# Features

## Necessities

### Game loop

The core experience is separated into **months** and **days**.

- Every month starts with a **payday** (calculated from the past month's work days), on which the player decides their budget — how much to spend on food, leisure, basic expenses, and bills (e.g. heating, which varies by month).
- Each following day the player can perform **one action**. That action is either:
  - responding to a random event, or
  - (if no event triggered) a status-affecting action such as:
    - **practicing a skill** — chance to improve the skill; the probability rises every time the player practices (to reward repeated effort) but resets on level-up to prevent exponential growth.
    - **resting** — restores energy and sanity.
- As the game progresses, **more advanced financial systems unlock** based on the player's progression score (see below).

#### Progression / unlock system

Access to more advanced financial products is gated by a **progression score**, which effectively functions as an XP system based on how well the player is managing their finances. Mechanically this is the player's in-game "credit score" — it rises with good behavior (paying bills on time, maintaining savings, responsibly using credit) and falls with bad behavior (missed payments, defaulting, overdrafts).

- Low score → only basic banking and high-risk predatory products (quick loans, BNPL) are available.
- Mid score → standard credit cards, regular loans, deposits.
- High score → investment products, mortgages, better loan rates.

> Open question: should this be a single combined score, or two separate tracks (a "credit score" for lenders and a separate "experience" for unlocks)? The doc currently treats them as one system for simplicity.

#### Game over conditions

If any of the four **player status stats** (health, hunger, sanity, energy) falls below a critical threshold, the game ends. The game-over screen shows a different message depending on which stat caused the failure — for example:

- **Health → 0:** hospitalization / death flavor text.
- **Hunger → 0:** malnutrition / collapse flavor text.
- **Sanity → 0:** burnout / breakdown flavor text.
- **Energy → 0:** total exhaustion flavor text.

> The exact thresholds and whether "0" or "below X" triggers game over is a design decision still open.

---

### Finance simulation system

- **Bank accounts and general banking** — checking accounts, savings accounts, savings goals.
- **Investment systems** — deposits and either stocks or funds (funds are simpler to model). Risk must be real, and **diversification must matter** — a single stock or fund can fail and the player loses some of the money.
- **Credit system** — credit score, loans, credit cards. High-risk products are explicitly represented (BNPL, quick loans, payday loans) with brutal penalty rates when not paid on time. The game is intentionally **educational and unforgiving**: budgeting is often not easy after a few bad decisions.
- **Incentives to spend** — vacations, or moving closer to a better job. Also **unavoidable large expenses** that randomly occur (medical bills, car repairs, burst pipes).
- **Loans for sudden costs** — available, but increasingly expensive / hard to get as the player's credit score drops.
- **Taxes** — deducted automatically from pay. They are visible to the player but not paid by hand (to keep the MVP simple). Taxes also apply when buying or renting out a house.

---

### System of AI Generated Events (SAGE)

Events should be grounded in real-life situations and plausible. Each event has:

- A **text** describing the situation.
- **2–4 response options**. Each option affects one or more player stats positively or negatively, scaled to the size of the event.

Events are **influenced by the game state**:

- Player **stats** (e.g. low hunger hurts performance at work).
- Player **possessions** (e.g. a shoddy house tends to produce leaky-pipe events).
- Player **skills** (e.g. high physique increases the number of available menial side jobs).

Where appropriate, a response option can be a **skill check**: the client rolls a value from 1–20 and compares it against a **difficulty class** provided by the model. Higher class = harder check.

> Example event:
> *"Your landlord texts: the boiler needs replacing. Your share of the emergency repair bill is \$180, due by Friday. You could pay it (subtract money), or claim you never got the message and delay (charisma check; on fail, subtract money and sanity)."*

If a skill check **fails**, the consequences are more severe than the non-check options. If a check **passes**, the described outcome happens cleanly.

#### LLM API contract

The Django backend exposes an endpoint (e.g. `POST /api/sage/event/`) that the client calls when a new event needs to be generated. The server builds a prompt from the supplied game state and instructs the LLM to reply with **JSON only, matching a fixed schema**. The server validates the response before returning it to the client; if validation fails, it retries or falls back to a canned event.

**Request — client to server:**

```json
{
  "day": 47,
  "month": 2,
  "player": {
    "stats": { "health": 72, "hunger": 40, "sanity": 55, "energy": 30 },
    "skills": { "cooking": 3, "handiwork": 1, "charisma": 5, "physique": 2 },
    "money": 412.50,
    "credit_score": 620
  },
  "house": {
    "type": "rented_apartment",
    "shoddiness": 7,
    "durability": 3,
    "distance_to_work_km": 18
  },
  "recent_events": ["missed_credit_card_payment", "worked_overtime"],
  "seed": 918273
}
```

**Server → LLM prompt (shape):**

The server wraps the state with a system prompt that:

1. Describes the game and tone (realistic, slightly unforgiving, educational).
2. Lists the allowed stat keys and the valid range of deltas.
3. Requires output to be **valid JSON only**, no prose, no markdown fences.
4. Provides the current game state as context.
5. Instructs the model to pick an event plausibly influenced by that state.

**LLM → server response (the schema the client receives):**

```json
{
  "event_id": "boiler_repair_bill",
  "title": "Landlord: boiler emergency",
  "sender": "Landlord",
  "body_markdown": "Your landlord texts: the boiler needs replacing. Your share of the emergency repair bill is **$180**, due by Friday.",
  "tone": "urgent",
  "options": [
    {
      "id": "pay",
      "label": "Pay the $180",
      "skill_check": null,
      "effects": {
        "money": -180,
        "sanity": -5
      },
      "description": "You pay it. Annoying, but handled."
    },
    {
      "id": "dodge",
      "label": "Claim you never got the message",
      "skill_check": {
        "skill": "charisma",
        "difficulty_class": 14
      },
      "effects_on_success": {
        "sanity": -2
      },
      "effects_on_failure": {
        "money": -220,
        "sanity": -15,
        "credit_score": -10
      },
      "description": "If he buys it, you're off the hook. If not, it's worse."
    }
  ]
}
```

Notes on the contract:

- `effects` (for non-check options) and `effects_on_success` / `effects_on_failure` (for check options) use the same fixed set of keys the client knows how to apply.
- The client owns the random roll for skill checks — the LLM only provides the difficulty class. This keeps determinism client-side and prevents the model from silently deciding outcomes.
- `event_id` is a slug the server can use for logging and for deduplicating events if needed.

> Open question: whether to cache/prefetch events (generate N in advance at payday) or generate them on-demand. On-demand is simpler; prefetching hides LLM latency.

---

### UI

**Main UI:** a phone mockup. The bottom dock is always visible and contains **4 apps**:

1. **Email** — displays random events and lets the player choose their response.
   - List view: sender, date (day/month), subject, and a status indicator.
   - Detail view: event description rendered as markdown, with 2–4 response cards below. Each card shows which skill is involved (if any) and a base success probability. Outcomes are revealed after the player commits — no preview spoiler.
2. **Home management app** — shows upcoming scheduled events (payday, credit card payments, etc.) and lets the player manage the house.
3. **Bank app** — manages all financial products (accounts, credit, debt), with graphs and budgeting tools.
4. **Health app** — shows the player's status values (mood, calorie count, sleep quality, etc.).

**Vibe:** pixelated, with a futuristic lean — holographic / cyberpunk / retro-futuristic aesthetic.

---

### Player character status system

- **Status stats:** health, hunger, sanity, energy.
- **Skills:** cooking, handiwork, charisma, physique.

---

### House status system

- Every house has **shoddiness** and **durability**.
- Better houses are less shoddy and closer to work → lower ongoing costs (heating, repairs, transport).
- Houses can still break down if random events are ignored.
- Higher durability → less likely to break down. Lower durability → more likely to break down further when damaged.
- **Flavor text** (description) is generated by the LLM for each specific house and reflects its underlying stats.
- Houses can be **rented, bought, and sold**. Owning more than one produces passive income (rent minus taxes).

---

## Nice to have

- Cute mascot.
- Music.
- Polished GUI with animations and nicer graphics.

---

## God-tier wants (probably out of scope for the hackathon)

- Fully polished and tested game experience.
- Login and registration (not needed for MVP, since state is in `localStorage`).
