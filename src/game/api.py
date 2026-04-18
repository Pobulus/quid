import random

from ninja import Body, NinjaAPI
from ninja.errors import HttpError

from game import sage, events, finance
from game.state import GameState, new_game

api = NinjaAPI(title="QUID API")


def _load(payload: dict) -> GameState:
    try:
        return GameState.from_dict(payload["state"])
    except (KeyError, ValueError) as e:
        raise HttpError(400, f"invalid state: {e}")


def _out(state: GameState, **extra) -> dict:
    return {"state": state.to_dict(), **extra}


@api.post("/new-game")
def new_game_endpoint(request, payload: dict | None = Body(None)):
    demo = bool((payload or {}).get("demo", False))
    return {"state": new_game(demo=demo).to_dict()}


@api.post("/echo")
def echo(request, payload: dict = Body(...)):
    state = _load(payload)
    return _out(state)


@api.post("/advance-day")
def advance_day(request, payload: dict = Body(...)):
    state = _load(payload)
    if events.budget_required(state):
        return _out(state, logs=[], reason="budget_required")
    state, logs = events.advance_day(state)
    return _out(state, logs=logs)


@api.post("/advance-until-event")
def advance_until_event(request, payload: dict = Body(...)):
    state = _load(payload)
    state, logs, reason, event = events.advance_until_event(state)
    return _out(state, logs=logs, reason=reason, event=event)


@api.post("/set-budget")
def set_budget(request, payload: dict = Body(...)):
    state = _load(payload)
    state, msg = events.set_budget(state, payload.get("budget", {}))
    return _out(state, message=msg)


@api.post("/practice-skill")
def practice_skill(request, payload: dict = Body(...)):
    state = _load(payload)
    state, msg = events.practice_skill(state, payload["skill"])
    return _out(state, message=msg)


@api.post("/rest")
def rest(request, payload: dict = Body(...)):
    state = _load(payload)
    state, msg = events.rest(state)
    return _out(state, message=msg)


@api.post("/transfer")
def transfer(request, payload: dict = Body(...)):
    state = _load(payload)
    direction = payload.get("direction")
    try:
        amount = int(payload.get("amount", 0))
    except (TypeError, ValueError):
        raise HttpError(400, "amount must be an integer (grosze)")
    try:
        state, msg = finance.transfer(state, direction, amount)
    except ValueError as e:
        raise HttpError(400, str(e))
    return _out(state, message=msg)


@api.post("/apply-cc")
def apply_cc(request, payload: dict = Body(...)):
    state = _load(payload)
    tier = payload.get("tier")
    try:
        state, msg = finance.apply_for_credit_card(state, tier)
    except ValueError as e:
        raise HttpError(400, str(e))
    return _out(state, message=msg)


# ---- SAGE (mock) ----------------------------------------------------------------


@api.post("/sage/event")
def sage_event(request, payload: dict = Body(...)):
    """Mock event source: always returns an event for now (no probability gate).

    The probability formula is computed and returned for the client to display /
    use later, but the mock fires unconditionally per the Phase 1 Track B
    instruction ("served immediately at random"). Real LLM-backed version will
    flip `force=False` and respect the gate.
    """
    state = _load(payload)
    force = bool(payload.get("force", True))

    rng = random.Random()
    prob = sage.event_probability(state)

    if not force and rng.random() > prob:
        return {"state": state.to_dict(), "event": None, "probability": prob}

    if sage.OLLAMA_AVAILABLE:
        event, source = sage.generate_event_via_llm(state, sage.call_ollama, rng=rng)
    else:
        event, source = sage.generate_event(state, rng=rng), "fallback"
    sage.push_to_inbox(state, event)
    return {"state": state.to_dict(), "event": event, "probability": prob, "source": source}


@api.post("/sage/prefetch")
def sage_prefetch(request, payload: dict = Body(...)):
    """Generate one event in the background and return it.

    Client fires this while the user is idle; on success it appends the event
    to its local `state.flags.event_queue`. The server does NOT return a new
    state — the client's state may have moved on by the time this returns, so
    we only hand back the validated event payload.
    """
    state = _load(payload)
    if sage.OLLAMA_AVAILABLE:
        event, source = sage.generate_single_event_via_llm(state, sage.call_ollama)
    else:
        event, source = sage.generate_event(state), "fallback"
    return {"event": event, "source": source}


@api.post("/event/resolve")
def event_resolve(request, payload: dict = Body(...)):
    state = _load(payload)
    event_id = payload.get("event_id")
    option_id = payload.get("option_id")
    if not event_id or not option_id:
        raise HttpError(400, "event_id and option_id are required")
    try:
        if event_id.startswith("cal_"):
            resolution = events.resolve_calendar_event(state, event_id, option_id)
        else:
            resolution = sage.resolve_event(
                state,
                event_id=event_id,
                option_id=option_id,
                roll_d20=int(payload.get("roll_d20", 0)),
            )
    except (ValueError, KeyError) as e:
        raise HttpError(400, str(e))
    return {"state": state.to_dict(), "resolution": resolution}
