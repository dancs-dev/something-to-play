# Next Play

Save games you like or dislike, explain why, and get personal recommendations from an OpenAI-compatible AI provider.

## Run

With [uv](https://docs.astral.sh/uv/) installed and either local Ollama running or an AI provider configured:

```bash
uv sync --locked
uv run python manage.py migrate
uv run python manage.py runserver
```

Open <http://localhost:8000>, create an account, add games and reasons, then click **Find my next game**. That request calls the configured provider immediately and returns its suggestions. There is no worker or scheduled import job.

The defaults are `http://localhost:11434/v1` and `qwen3.5:latest`, so a local Ollama instance works without a key. To use another OpenAI-compatible Chat Completions provider, set `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL`, and, when required, `OPENAI_COMPATIBLE_API_KEY` in the environment or `.env`. For example, use `https://api.openai.com/v1` for OpenAI or `https://openrouter.ai/api/v1` for OpenRouter, and set the model ID supported by that provider. The endpoint and selected model must support Chat Completions with JSON mode.

Keep provider keys server-side; do not put them in templates or browser code. The recommendation request sends the user's saved game preferences and optional request to the configured provider. To load `.env`, run:

```bash
uv run --env-file .env python manage.py runserver
```

The ordinary command works without `.env`. Set `STEAM_WEB_API_KEY` in the server environment to enable optional Steam profile lookup, library sync, and catalogue search. No Steam sign-in is used: users enter a Steam ID or profile URL, so the association is unverified and the profile's game details must be visible. The key stays on the server. `uv` maintains `pyproject.toml` and the committed `uv.lock`. No frontend build is needed; templates and the locally vendored HTMX asset handle the interface.

## How it works

Games have local titles and optional provider identities, such as a Steam app ID. Linked profiles and ownership are stored separately from each user's Like, Dislike, or Ignore choice and note. A sync runs only when the user presses **Sync library**; it adds new games, marks games missing from the latest visible library as inactive, and records the sync time. It never changes a user's choice or note. Manual game search checks local games first, then loads Steam's game catalogue on the first miss; later catalogue refreshes require an explicit button press and request only apps changed since the previous successful refresh. Games outside Steam can be saved by title.

The app sends that user's Like and Dislike list and an optional current request to the configured provider. Ownership and Ignore are excluded. The model returns game titles, personal rationales and potential drawbacks. JSON responses are validated; duplicate and already-listed games are removed. The app saves successful responses so users can revisit them. “Liked it” / “Disliked it” opens an editable taste entry so the user can add their reason.

The configured model uses its knowledge. These are AI suggestions, not verified catalogue facts. A model/network error is displayed honestly with a retry path rather than replaced by unrelated picks. The request has a 90-second timeout. With a hosted provider, the user's taste and request are sent to that provider. Steam profile IDs and owned games are stored locally; Steam receives the profile ID only during a user-requested link or sync.

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
