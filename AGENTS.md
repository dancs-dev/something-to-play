# Repository Guidelines

## Project Structure & Module Organization

This is a Django game recommendation app. `config/` holds project settings and URL routing; `app/` holds models, views, forms, Steam integration, library sync, recommendation logic, and management commands. Database changes belong in `app/migrations/`. HTML templates live in `app/templates/`, and CSS and JavaScript assets live in `app/static/app/`. Tests are in `app/tests/`: `test_app.py` covers application behavior, `test_steam.py` covers Steam integration, `test_browser.py` exercises the browser flow, and `test_eval.py` covers the eval command.

## Build, Test, and Development Commands

Use Python 3.12 or later and `uv`.

- `uv sync --locked`: install the locked dependencies.
- `uv run python manage.py migrate`: apply database migrations.
- `uv run --env-file .env python manage.py runserver`: start the local server with provider settings. Omit `--env-file .env` when no local configuration is needed.
- `uv run python manage.py test`: run the Django test suite.
- `uv run pre-commit run --all-files`: run formatting, linting, type checks, and secret scanning.

Run `uv run playwright install chromium` before the browser smoke test if Chromium is missing. There is no separate build step.

## Coding Style & Naming Conventions

Use four spaces for Python indentation, double quotes, and Python 3.12 syntax. Follow existing `snake_case` names for modules, functions, and tests, and `PascalCase` for classes. Keep Django views and forms in their existing modules and add migrations when models change. Ruff formats and lints Python; mypy checks types. Run `uv run ruff format .`, `uv run ruff check .`, and `uv run mypy .` before submitting changes.

## Testing Guidelines

Use Django `TestCase` for application tests and `StaticLiveServerTestCase` with Playwright for browser checks. Name test methods `test_*` and put new cases beside related coverage in `app/tests/`. Mock external provider and Steam calls so tests run without API credentials. No coverage threshold is configured.

## Commit & Pull Request Guidelines

Recent commits use short, imperative, sentence-case subjects such as `Fix playwright tests` and `Add Steam integration`. Keep each commit focused. In pull requests, summarize behavior, mention relevant issues, list checks run, and include screenshots for visible UI changes.

## Configuration & Secrets

Copy settings from `.env.example` into a local `.env`. Keep Steam and AI provider keys server side and out of commits. The pre-commit configuration runs `detect-secrets` against `.secrets.baseline`.
