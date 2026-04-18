from ninja import Body, NinjaAPI

from game.state import GameState, new_game

api = NinjaAPI(title="QUID API")


@api.post("/new-game")
def new_game_endpoint(request):
    return {"state": new_game().to_dict()}


@api.post("/echo")
def echo(request, payload: dict = Body(...)):
    state = GameState.from_dict(payload["state"])
    return {"state": state.to_dict()}
