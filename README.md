# QUID — Quest for Understanding Income & Debt

A deliberately unforgiving financial-literacy RPG. Browser game; Django serves the shell and proxies LLM calls; all gameplay state lives in the browser's `localStorage`.

See `docs/Hackathon_project.md` for the design spec and `agent/TASKS.md` for the build plan.

## Setup (first time)

```sh
python3 -m venv .venv
.venv/bin/pip install 'django>=5.1,<6' django-ninja python-dotenv 'pydantic>=2' httpx
cp .env.example .env        # edit OLLAMA_HOST / OLLAMA_MODEL
cd src
../.venv/bin/python manage.py migrate
```

## Run

```sh
cd src
../.venv/bin/python manage.py runserver
```

Open <http://localhost:8000/>.

The page boots from `localStorage` or calls `/api/new-game` on first visit.
Use the **"load fake"** button (bottom-left of the screen) to populate all four apps with rich demo data without needing Track A/B endpoints.

## Environment

`.env` at the repo root:

| var                  | what                                                     |
|----------------------|----------------------------------------------------------|
| `DJANGO_SECRET_KEY`  | any random string for dev                                |
| `DJANGO_DEBUG`       | `1` for dev                                              |
| `OLLAMA_HOST`        | full URL to the Ollama server (Tailscale Mac in our case) |
| `OLLAMA_MODEL`       | e.g. `gemma3:4b`                                         |

## Smoke test

```sh
curl -X POST http://localhost:8000/api/new-game
curl -X POST -H 'Content-Type: application/json' \
     -d "$(curl -s -X POST http://localhost:8000/api/new-game)" \
     http://localhost:8000/api/echo
```

The echo endpoint round-trips state through `to_dict`/`from_dict` — same input out means the schema didn't drift.
