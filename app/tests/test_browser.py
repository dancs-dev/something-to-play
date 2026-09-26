"""Optional real-browser smoke: uv run --with playwright python manage.py test app.tests.test_browser."""
from importlib.util import find_spec
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import override_settings

from app.models import RecommendationRun


@skipUnless(find_spec('playwright'), 'Optional: run with uv run --with playwright after installing Chromium')
@override_settings(AI_ENABLED=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class BrowserSmoke(StaticLiveServerTestCase):
    def test_manual_flow_mobile_and_htmx(self):
        from playwright.sync_api import sync_playwright
        call_command('seed_demo', stdout=StringIO())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.get_by_role('link', name='Get started', exact=True).click()
            page.get_by_label('Username').fill('browser-player')
            page.get_by_label('Password:', exact=True).fill('browser-smoke-password-82')
            page.get_by_label('Password confirmation').fill('browser-smoke-password-82')
            page.get_by_role('button', name='Create account').click()
            page.get_by_label('Game, mechanic or theme').fill('Portal 2')
            page.get_by_label('What worked for you, or got in the way?').fill('Puzzles and exploration')
            page.get_by_role('button', name='Save answer').click()
            page.get_by_role('link', name='Taste profile', exact=True).click()
            page.get_by_label('Game:', exact=True).select_option(label='Portal 2')
            page.get_by_label('Owned:', exact=True).select_option('yes')
            page.get_by_role('button', name='Update ownership').click()
            page.get_by_role('link', name='◈ Next Play').click()
            page.get_by_label('Minutes available').fill('45')
            page.get_by_label('Choose from').select_option('owned')
            page.get_by_label('How are you playing?').select_option('solo')
            page.get_by_role('button', name='Find my next game').click()
            page.locator('.card').wait_for()
            self.assertEqual(page.locator('.card h2').all_text_contents(), ['Portal 2'])
            self.assertEqual(page.evaluate('typeof htmx'), 'object')
            self.assertTrue(page.get_by_text('Demonstration data · curated estimates').is_visible())
            # Exercise the actual polling swap, not only an HTMX-marked Django request.
            # Playwright's sync API runs an event loop; keep Django ORM calls on a separate thread.
            with ThreadPoolExecutor(max_workers=1) as db:
                db.submit(lambda: RecommendationRun.objects.update(status='pending')).result()
                page.reload()
                db.submit(lambda: RecommendationRun.objects.update(status='fallback')).result()
            page.get_by_text('AI could not improve this run.', exact=False).wait_for(timeout=10000)
            page.screenshot(path='/tmp/game-recommender-desktop.png', full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.screenshot(path='/tmp/game-recommender-mobile.png', full_page=True)
            page.get_by_role('button', name='Not tonight', exact=True).click()
            page.get_by_text('No eligible picks right now.').wait_for()
            self.assertEqual(errors, [])
            browser.close()
