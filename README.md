# Next Play

A Django + SQLite game recommendation MVP. Start with local accounts, manual taste and ownership, and 20 labelled demonstration games. Steam and OpenAI are optional.

## Run locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run these commands from the project root:

```bash
uv python install 3.12
uv sync --locked
uv run python manage.py migrate
uv run python manage.py seed_demo
uv run python manage.py createsuperuser  # optional admin access
uv run python manage.py runserver
```

Open <http://localhost:8000>. Sign up, answer the onboarding questions, then select your session constraints. The taste profile lets you edit preferences, record ownership, connect Steam and review AI suggestions. `/admin/` exposes the catalogue and evidence for editorial maintenance. `uv sync` also works; use `--locked` in CI to reject lockfile drift.

Python is pinned in `.python-version`. Dependencies and their exact resolution live in `pyproject.toml` and `uv.lock`; use `uv add` / `uv remove` for changes. HTMX 2.0.8 is vendored locally with its upstream license; no Node build or CDN is needed at runtime. Forms work without JavaScript; HTMX polls optional enhancement status.

For environment configuration, copy `.env.example` to `.env`, fill only the integrations you need, and use uv's environment-file support:

```bash
uv run --env-file .env python manage.py runserver
```

Use the same `--env-file .env` option on worker/import commands. Files are not automatically loaded by Django. Keep `.env`, SQLite databases, `.cache` and `.venv` out of version control.

## Product behavior

- Onboarding asks about favourites, dislikes and the reasons behind them. Unresolved free-text titles remain valid preferences and can match later catalogue additions.
- Manual preferences are explicit and take precedence over inference. Conversation extraction creates tentative proposals; the user edits/confirms them before they affect ranking.
- Minutes, energy, operating system, play mode and purchase scope are temporary run inputs. They never overwrite lasting taste.
- Platform, ownership and solo/co-op/competitive requirements are hard filters. Unknown compatibility fails a required filter. Time/energy suitability is scored and mismatches are explained, rather than hidden by silently relaxing constraints.
- A manual ownership choice overrides Steam evidence. “Use Steam / unknown” removes that override. “New to my library” means **not known to be owned**, since Steam/manual data may be incomplete; it is not proof that a purchase is necessary.
- “Good fit” is positive feedback. “Not tonight” hides a game for the same session choices on the same UTC date, without changing taste. Changing choices starts a different context. “I dislike this game” creates an explicit lasting dislike; edit/remove it in the taste profile to undo it.
- Runs retain input snapshots, scoring weights/version, component scores, evidence, results and integration diagnostics. Each user can inspect only their own history; staff can inspect records through admin.

## Deterministic ranking

`app/recommendations.py` is shared by views and the worker. It retrieves games with ordinary ORM queries, filters constraints, and scores in Python. No embeddings/vector database is required.

`SCORE_WEIGHTS` in Django settings defaults to taste 0.50, session 0.35, engagement 0.05 and feedback 0.10. Taste uses direct game preferences, mechanics/themes and overlap with liked/disliked games. Session suitability combines estimated duration, attention and intensity, giving pause flexibility partial credit for shorter available sessions. Unknown suitability is neutral, not falsely precise. Aggregate playtime contributes a small capped/log-scaled engagement signal; it never creates a like/dislike preference. Missing feedback is neutral. Ties use the local game ID.

The selector keeps one candidate per available play style before filling up to six results. It does not label a long game “immersive tonight” if its estimated duration exceeds the current budget. The full candidate scores and weighted components remain inspectable.

Demonstration records are real game titles with **curated, unverified estimates**, prominently labelled on cards. They are not guaranteed current platform/mode facts and contain no fabricated reviews. Run the metadata command to verify an individual game, or edit it with source evidence in admin. Reseeding never overwrites existing catalogue edits.

## Steam

Set `STEAM_API_KEY` for library imports. Steam OpenID identity verification itself does not require that key. Set `SITE_ORIGIN` to the exact origin used in your browser, e.g. `http://localhost:8000`; do not alternate with `127.0.0.1` during login.

Create a local account and connect Steam from the taste profile. Later, “Sign in with Steam” can access that linked account. The callback validates the fixed Steam provider, namespace/mode, claimed identity, signed critical fields, callback URL, session-bound state, nonce age and one-time nonce, and calls Steam server-side with `check_authentication`. Duplicate links/account merging are rejected. The callback never imports a library.

```bash
uv run --env-file .env python manage.py sync_steam
uv run --env-file .env python manage.py sync_steam --user 1
uv run --env-file .env python manage.py sync_steam --user 1 --force
uv run --env-file .env python manage.py sync_catalogue 620
uv run --env-file .env python manage.py sync_reviews 620
```

- `sync_steam`: owned games and available lifetime/recent playtime. Complete valid empty libraries are different from private/unavailable or malformed responses. Failed library reads preserve prior data. If only recent activity fails, owned data is updated, current recent minutes become unknown, prior snapshots remain, and status becomes `partial`.
- Imports upsert unique user/game ownership and only snapshot changed playtime (or a first observation). Imported games initially have unknown compatibility; seed overlap, `sync_catalogue`, admin evidence, or AI grounding can supply it. Steam data never overwrites manual ownership.
- `sync_catalogue`: verifies game identity, native Windows/macOS/Linux listing and positive solo/co-op/PvP metadata. Generic multiplayer is not proof of PvP. Missing category evidence remains unknown. No AI key is needed.
- `sync_reviews`: up to 20 recent English community reviews for a game; external IDs deduplicate updates. These are sampled opinions, not the connected user's history. Complete personal review-history import is deferred pending a verified supported method.
- HTTP calls have 5-second connection/10-second operation timeouts, three attempts maximum on transient failures, bounded backoff and capped `Retry-After`. Other failures do not retry. Successful responses are cached privately for 15 minutes (library) or 24 hours (reviews/store metadata). `--force` bypasses the library cache.
- Aggregate/recent playtime is **not session history or proof of enjoyment**. Privacy, unavailable games, free/shared entitlements and API coverage prevent claiming a complete ownership inventory.

Official references: [Steam OpenID](https://partner.steamgames.com/doc/features/auth), [player APIs](https://partner.steamgames.com/doc/webapi/IPlayerService), [game reviews](https://partner.steamgames.com/doc/store/getreviews), [OpenID 2.0 verification](https://openid.net/specs/openid-authentication-2_0.html#verification).

**Store metadata limitation:** grounding uses Steam's public `store.steampowered.com/api/appdetails` endpoint, which does not have the same supported contract as documented Web APIs. It may be blocked, region-dependent or change schema. Only independently returned matching identities and recognised metadata are accepted; failure leaves the candidate unverified and excluded. The fixed `cc=us` is for metadata retrieval, not a claim of regional availability or pricing. Steam Deck/Proton compatibility and current purchase prices are not inferred.

## Optional OpenAI assistance

Set `OPENAI_API_KEY` and, optionally, `OPENAI_MODEL` (default `gpt-4.1-mini`). The configured model/account must support Responses structured outputs and the web-search tool. The provider interface in `app/llm.py` has preference extraction, candidate discovery and reranking methods; a different provider can implement the same validated schemas without changing scoring/views.

- Extraction runs on submitted conversation text, with at most the two preceding turns. It saves the note first, uses a bounded request, and preserves a manual path on any provider/schema failure.
- Recommendations return deterministic results immediately. With AI configured, the run queues optional enhancement for the single worker.
- Discovery proposes up to ten Steam titles from model knowledge and can search Steam store pages (at most three tool calls). It sees relevant preferences/session data and up to 40 owned titles, never credentials, user IDs or Steam IDs. Owned-only discovery is restricted to those known owned titles.
- A proposed title/app ID is a hypothesis. Server-side grounding fetches the fixed Steam endpoint, checks identity and platform/mode schema, and stores evidence. Model-supplied URLs are never fetched or treated as verification. Subjective attributes without supplied evidence remain unknown.
- Reranking receives at most 20 eligible candidates, their evidence facts and up to two bounded review excerpts per game. Prompts are versioned; inputs/results are recorded on the run. User/review/search text is untrusted data, never instructions.
- Reranking must return every supplied ID exactly once and only known evidence keys. The application renders rationale text from selected stored facts; the model cannot introduce factual prose or unknown game IDs. Constraints are checked again after the network call, and variety is restored.
- The SDK has a 30-second timeout and no automatic retries; token/tool/candidate limits bound work. Missing keys, provider refusals, incomplete/malformed output and failures use deterministic fallback. No recursive agent loops or MCP are used. `store=False` is sent to OpenAI; normal provider retention policies still apply.

[Responses web search](https://developers.openai.com/api/docs/guides/tools-web-search), [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [default model capabilities](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

## Single worker and SQLite

```bash
uv run --env-file .env python manage.py run_pending          # one pass
uv run --env-file .env python manage.py run_pending --watch  # poll every five seconds
```

The sync and worker commands share an OS file lock and must run on the same host/filesystem. This implementation uses `fcntl`, so run on Linux/macOS or WSL. Do not start separate writers or cron jobs on other hosts against this SQLite file. A crashed process releases its lock; unfinished enhancement leases and Steam sync attempts are reclaimable after five minutes. The worker processes jobs serially; importing and network work never hold a database transaction. Library writes are batched in groups of 100.

SQLite still permits only **one writer at a time**, including web requests, sessions and the worker. Transactions are short and the busy timeout is five seconds; this is not a throughput guarantee. Keep this MVP on one host with modest traffic. A paused/unstarted worker leaves usable deterministic results, with pending status visible. No Redis or Celery is required.

Back up the SQLite file using SQLite's backup facility, or stop web/worker processes before copying it; do not copy an actively written file as a production backup. The private file cache must never be served as static media or made writable by untrusted users. It is disposable; the database is the source of truth.

### Moving to PostgreSQL

1. Stop writers and back up SQLite. Export while still using SQLite:
   `uv run python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission --exclude sessions --exclude app.openidnonce --output /safe/path/data.json`
2. Add the driver with `uv add 'psycopg[binary]'`. Provision an empty PostgreSQL database and set `DB_ENGINE=postgresql` plus the documented `DB_*` variables.
3. Run `uv run --env-file .env python manage.py migrate`, then `uv run --env-file .env python manage.py loaddata /safe/path/data.json`. Django resets fixture-loaded sequences; verify next inserts in the rehearsal.
4. Run the tests against a dedicated PostgreSQL test database, compare user/game/ownership/preference/run counts, and verify representative histories and account links before reopening traffic. Keep the backup for rollback.

The application uses portable ORM types, constraints and JSON snapshots, not SQLite-specific query SQL. PostgreSQL is a documented migration path, not a tested production deployment. Reconsider queue infrastructure only when measured throughput requires it.

## Verification and delivery status

```bash
uv run python manage.py test
uv run python manage.py check
uv run python manage.py makemigrations --check --dry-run
```

For the optional real-browser check (desktop/mobile layout and an actual HTMX swap):

```bash
uv run --with playwright python -m playwright install chromium
uv run --with playwright python manage.py test app.tests.test_browser
```

The regular suite skips that browser test unless Playwright is available. It runs against a disposable test database, not your local accounts. Screenshots are written to `/tmp/game-recommender-desktop.png` and `/tmp/game-recommender-mobile.png`.

The automated suite uses mocked external transports; it does not need keys or network. It covers local signup/onboarding, account isolation, CSRF, Steam OpenID verification/replay/link collisions, unavailable and partial data, idempotent imports/seeds, bounded retries/cache, hard constraints and modes, feedback semantics, grounding, malformed/unknown AI outputs, fallback and worker locking/recovery.

For production, set `DJANGO_DEBUG=0`, a unique random `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and the HTTPS `SITE_ORIGIN`; configure HTTPS/static-file serving and run `uv run --env-file .env python manage.py check --deploy`. The development server is not a deployment setup. Password-reset email, public-registration abuse controls, backups/monitoring and a hosting configuration remain deployment work.

Live Steam-account login/library import and paid OpenAI calls require your credentials and have not been smoke-tested in this workspace. The adapter paths are implemented and tested with recorded-shape fixtures/mocks, not claimed as live-account verification. No credentials are bundled. Complete personal review history, a store-wide crawler, console support, pricing, inferred Steam Deck support, Celery/Redis and vector search are intentionally deferred.
