# Next Play

Save games you like or dislike, explain why, and ask local Ollama what to play next.

## Run

With [uv](https://docs.astral.sh/uv/) installed and Ollama running locally:

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py runserver
```

Open <http://localhost:8000>, create an account, add games and reasons, then click **Ask Ollama for recommendations**. That request calls Ollama immediately and returns its suggestions. There is no worker, import job, scoring pipeline or seeded catalogue.

The defaults are `http://localhost:11434/v1` and `qwen3.5:latest`, matching the local instance used during development. No API key is needed. To use a different installed model or endpoint, set `OLLAMA_MODEL` / `OLLAMA_BASE_URL` in the environment, or in `.env` and run:

```bash
uv run --env-file .env python manage.py runserver
```

The ordinary command works without `.env`. `uv` maintains `pyproject.toml` and the committed `uv.lock`. No frontend build is needed; templates and the locally vendored HTMX asset handle the interface.

## How it works

There are two application models: `Preference` (user, game name, like/dislike, reason) and `RecommendationRun` (user, saved input and AI response).

The app sends that user's complete taste list and an optional current request directly to Ollama. The model returns game titles, personal rationales and potential drawbacks. JSON responses are validated; duplicate and already-listed games are removed. The app saves successful responses so users can revisit them. “Liked it” / “Disliked it” opens an editable taste entry so the user can add their reason.

Ollama uses its knowledge. These are AI suggestions, not verified catalogue facts; there is no web search or Steam connection. A model/network error is displayed honestly with a retry path rather than replaced by unrelated picks. The request has a 90-second timeout. Nothing is sent to a hosted AI provider.

Accounts use Django authentication and CSRF protection. Queries are scoped to the signed-in user. SQLite writes occur after the model request, with no transaction held during inference.

## Tests

```bash
uv run python manage.py test
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

The regular suite uses mocked model responses and requires no running model. It covers the direct HTTP call, account isolation, editing taste, error handling, validation, escaping and feedback. An optional browser smoke test checks the full form/HTMX flow and mobile layout:

```bash
uv run --with playwright python -m playwright install chromium
uv run --with playwright python manage.py test app.tests.test_browser
```

## Existing installations

The simplification migrations preserve taste entries (including their reasons) and past recommendation inputs/results. The old Steam/catalogue/activity/conversation tables are removed. Back up an existing database before migrating if those legacy records matter. A complete backup of this workspace's pre-simplification database is retained privately in `.cache/before-simplification-*.sqlite3`.

Earlier migrations stay in the repository so existing databases can upgrade normally; they do not represent active app features. Stop old worker processes and restart the web server when upgrading. Old deterministic recommendation results remain visible in history; new requests are direct AI responses.

For deployment, set `DJANGO_DEBUG=0`, a strong `DJANGO_SECRET_KEY` and appropriate `DJANGO_ALLOWED_HOSTS`, configure HTTPS/static serving, and run `uv run python manage.py check --deploy`. SQLite is intended for modest single-host usage; it has one writer at a time. The Django development server is for local use.
