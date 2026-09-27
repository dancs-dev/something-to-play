# Something to Play

Something to Play keeps your game ratings and reasons, then uses an OpenAI-compatible model to suggest what to play next. Steam integration can look up profiles and sync owned games.

## Installation

### Prerequisites

- Python 3.12 or later and [uv](https://docs.astral.sh/uv/)

### Optional external providers

Ollama is the default AI provider. The app expects `http://localhost:11434/v1` and the model `gemma4:latest`. To use another OpenAI-compatible Chat Completions provider, set `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL`, and, if required, `OPENAI_COMPATIBLE_API_KEY` in `.env`. The model endpoint must support JSON mode.

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

### Formatting, linting, and type checking

```bash
uv run ruff format .
uv run ruff check .
uv run mypy .
```

### Tests

```bash
uv run python manage.py test
```
