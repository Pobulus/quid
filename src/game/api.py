import random

from ninja import Body, NinjaAPI

from game import sage
from game.state import GameState, new_game

api = NinjaAPI(title="QUID API")


@api.post("/new-game")
def new_game_endpoint(request):
    return {"state": new_game().to_dict()}


@api.post("/echo")
def echo(request, payload: dict = Body(...)):
    state = GameState.from_dict(payload["state"])
    return {"state": state.to_dict()}


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
    try:
        resolution = sage.resolve_event(
            state,
            event_id=payload["event_id"],
            option_id=payload["option_id"],
            roll_d20=int(payload["roll_d20"]),
        )
    except ValueError as e:
        return {"error": str(e)}, 400
    return {"state": state.to_dict(), "resolution": resolution}
