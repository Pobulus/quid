import random

from ninja import Body, NinjaAPI

from game import sage,events
from game.state import GameState, new_game

api = NinjaAPI(title="QUID API")


def _load(payload: dict) -> GameState:
    return GameState.from_dict(payload["state"])


def _out(state: GameState, **extra) -> dict:
    return {"state": state.to_dict(), **extra}


@api.post("/new-game")
def new_game_endpoint(request):
    return {"state": new_game().to_dict()}


@api.post("/echo")
def echo(request, payload: dict = Body(...)):
    state = _load(payload)
    return _out(state)


@api.post("/advance-day")
def advance_day(request, payload: dict = Body(...)):
    state = _load(payload)
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


# ---- SAGE (mock) ----------------------------------------------------------------


@api.post("/sage/event")
def sage_event(request, payload: dict = Body(...)):
    """Mock event source: always returns an event for now (no probability gate).

    The probability formula is computed and returned for the client to display /
    use later, but the mock fires unconditionally per the Phase 1 Track B
    instruction ("served immediately at random"). Real LLM-backed version will
    flip `force=False` and respect the gate.
    """
    state = GameState.from_dict(payload["state"])
    force = bool(payload.get("force", True))

    rng = random.Random()
    prob = sage.event_probability(state)

    if not force and rng.random() > prob:
        return {"state": state.to_dict(), "event": None, "probability": prob}

    event = sage.generate_event(state, rng=rng)
    sage.push_to_inbox(state, event)
    return {"state": state.to_dict(), "event": event, "probability": prob}


@api.post("/event/resolve")
def event_resolve(request, payload: dict = Body(...)):
    state = GameState.from_dict(payload["state"])
    event_id = payload["event_id"]
    option_id = payload["option_id"]
    try:
        if event_id.startswith("cal_"):
            resolution = events.resolve_calendar_event(state, event_id, option_id)
        else:
            resolution = sage.resolve_event(
                state,
                event_id=event_id,
                option_id=option_id,
                roll_d20=int(payload["roll_d20"]),
            )
    except ValueError as e:
        return {"error": str(e)}, 400
    return {"state": state.to_dict(), "resolution": resolution}
