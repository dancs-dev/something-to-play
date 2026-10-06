# Something to Play

I built Something to Play to answer a familiar question: which game in my library should I play next? It keeps your ratings and reasons, then uses an OpenAI-compatible model to suggest games with a reason to try each one and a possible drawback. Steam integration can sync your owned games.

This is intended for self-hosting by one person or a small group of trusted users. Sign-up is open to anyone who can reach the site, so restrict access at the network or reverse proxy if you host it beyond localhost.

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

### Docker Compose

Copy `.env.example` to `.env`, set a unique `DJANGO_SECRET_KEY` (for example, generate one with `python3 -c 'import secrets; print(secrets.token_urlsafe(50))'`), and set `OPENAI_COMPATIBLE_BASE_URL=http://host.docker.internal:11434/v1`. Compose connects to your existing Ollama without another Ollama image or model download. The app is available only at <http://localhost:8000>; its SQLite database lives in a named volume, and migrations run when the container starts. A second `worker` service runs `qcluster` to process recommendations in the background.

On Linux, Ollama must listen on an address the container can reach: its default `127.0.0.1:11434` bind is insufficient. Set `OLLAMA_HOST=0.0.0.0:11434` for the host Ollama service and restrict port 11434 with your firewall if the host is reachable from other machines.

With rootless Docker on Linux, `host-gateway` can point inside Docker's network namespace instead of reaching the host. If the connection fails, set `OPENAI_COMPATIBLE_BASE_URL=http://<host-LAN-IP>:11434/v1` in `.env`.

```bash
docker compose up --build -d
```

If you use another provider, set its URL and credentials in `.env` before starting Compose.

For a trusted HTTPS reverse proxy, also set `DJANGO_ALLOWED_HOSTS` to your hostname and `DJANGO_HTTPS=1` and `DJANGO_TRUST_PROXY_HEADERS=1` in `.env`. The proxy must overwrite `X-Forwarded-Proto`; keep the app port bound to localhost and restrict access at the proxy.

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

Recommendations run in the background, so also start a worker in a second terminal:

```bash
uv run --env-file .env python manage.py qcluster
```

The web request returns immediately and the page polls the run's status until the picks are ready. Without a running worker the loader stops with an error after a few minutes.

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

### Prompt evaluation

The `eval_recommendations` management command measures how different models and reasoning levels handle the recommendation prompt. Edit the `CASES` and `PROFILES` lists at the top of `app/management/commands/eval_recommendations.py` to define what gets compared: each case is a model plus optional reasoning effort, endpoint, and price overrides, and each profile is a fixed test player (their taste ratings, request, and library).

Requests go to a test case's own `base_url` and `api_key` when it has them, and otherwise to the app's configured endpoint and key. The default cases are OpenRouter models, so either set `OPENAI_COMPATIBLE_BASE_URL=https://openrouter.ai/api/v1` and your OpenRouter key as `OPENAI_COMPATIBLE_API_KEY` in `.env`, or add `base_url` and `api_key` to each case. A case with neither would hit the default local Ollama, which doesn't serve those model IDs, and fail.

Every case runs against every profile through the same code path the app uses. The command writes a Markdown and JSON report to `evals/` with time taken, tokens, cost (marked as reported by the provider or estimated from the case's price table), and automatic style flags for the prompt's writing rules, such as stock praise words or repeated sentence openers.

```bash
uv run --env-file .env python manage.py eval_recommendations
```

