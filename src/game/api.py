from ninja import Body, NinjaAPI

from game import events
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
    state, logs, reason = events.advance_until_event(state)
    return _out(state, logs=logs, reason=reason)


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
