# Something to Play

I built Something to Play to answer a familiar question: which game in my library should I play next? It keeps your ratings and reasons, then uses an OpenAI-compatible model to suggest games with a reason to try each one and a possible drawback. Steam integration can sync your owned games.

![Game suggestions after rating a game](docs/demo-home.png)

## Installation

### Prerequisites

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/)

### AI provider

Recommendations need an AI provider. By default, the app uses [Ollama](https://ollama.com/) at `http://localhost:11434/v1` with `gemma4:latest`. Install and start Ollama, then download the model:

```bash
ollama pull gemma4
```

To use another OpenAI-compatible Chat Completions provider, copy `.env.example` to `.env` and set `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL`, and, if required, `OPENAI_COMPATIBLE_API_KEY`. The model endpoint must support JSON mode.

### Optional Steam integration

Set `STEAM_WEB_API_KEY` in `.env` to enable Steam profile lookup, library sync, or catalogue search. Keep provider and Steam keys on the server. The app sends your saved ratings and recommendation request to the configured AI provider.

## Development

### Running locally

Install dependencies, then apply migrations and start Django:

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py runserver
```

With a `.env` file, start the server with:

```bash
uv run --env-file .env python manage.py runserver
```

Open <http://localhost:8000> and create an account.

### Code checks and secret scanning

Install the Git hooks once per clone:

```bash
uv run pre-commit install
```

Run all hooks manually across the repository with `uv run pre-commit run --all-files`.

```bash
uv run ruff format .
uv run ruff check .
uv run mypy .
```

### Tests

```bash
uv run python manage.py test
```
