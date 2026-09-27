import json
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import CatalogueState, Game, LinkedAccount, Ownership, Preference, RecommendationRun
from app.recommendations import RecommendationError, ask_provider, create_run

GAMES = [{'title': 'The Talos Principle', 'rationale': 'Its puzzle solving fits your reasons for liking Portal 2.',
          'drawback': 'The philosophical story may be slower than you want.'}]


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AppTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('alice', password='test-password')
        self.other = get_user_model().objects.create_user('bob', password='test-password')
        self.game = Game.objects.create(title='Portal 2')
        self.preference = Preference.objects.create(user=self.user, game=self.game, sentiment=1, reason='Clever puzzles')
        self.client.force_login(self.user)

    def test_signup_and_taste_crud(self):
        self.client.logout()
        response = self.client.post(reverse('signup'), {'username': 'new-player',
            'password1': 'a-long-password-872', 'password2': 'a-long-password-872'}, follow=True)
        self.assertContains(response, 'Add a game')
        self.client.post(reverse('preference_new'), {'subject': 'Outer Wilds', 'sentiment': 1, 'reason': 'Exploration'})
        pref = Preference.objects.get(game__title='Outer Wilds')
        self.assertNotEqual(pref.user_id, self.user.pk)
        self.client.post(reverse('preference_edit', args=[pref.pk]),
                         {'subject': 'Outer Wilds', 'sentiment': -1, 'reason': 'Actually, the time pressure'})
        pref.refresh_from_db()
        self.assertEqual(pref.reason, 'Actually, the time pressure')
        self.assertEqual(pref.sentiment, -1)
        self.client.post(reverse('preference_delete', args=[pref.pk]))
        self.assertFalse(Preference.objects.filter(pk=pref.pk).exists())

    def test_signup_errors_follow_their_fields_without_upfront_guidance(self):
        self.client.logout()
        self.assertNotContains(self.client.get(reverse('signup')), 'Your password must contain at least 8 characters.')
        response = self.client.post(reverse('signup'), {
            'username': 'alice', 'password1': '123', 'password2': '123',
        })
        html = response.content.decode()
        self.assertLess(html.index('name="username"'), html.index('A user with that username already exists.'))
        self.assertLess(html.index('A user with that username already exists.'), html.index('name="password1"'))
        self.assertLess(html.index('name="password2"'), html.index('This password is too short.'))

    def test_case_insensitive_duplicate_updates_existing_game(self):
        self.client.post(reverse('preference_new'), {'subject': 'PORTAL 2', 'sentiment': -1, 'reason': 'Changed my mind'})
        self.assertEqual(Preference.objects.filter(user=self.user).count(), 1)
        self.preference.refresh_from_db()
        self.assertEqual(self.preference.sentiment, -1)
        self.assertEqual(self.preference.reason, 'Changed my mind')

    def test_displayed_times_include_browser_convertible_instants(self):
        now = timezone.now()
        run = RecommendationRun.objects.create(user=self.user, inputs={}, results=GAMES)
        LinkedAccount.objects.create(user=self.user, provider='steam', external_user_id='123', last_synced_at=now)
        CatalogueState.objects.create(provider='steam', last_synced_at=now)
        for url, stamp in (
            (reverse('home'), run.created_at),
            (reverse('history'), run.created_at),
            (reverse('library'), now),
            (reverse('settings'), now),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, f'<time datetime="{stamp.isoformat()}"')
                self.assertContains(response, '/static/app/local-time.js')

    def test_recommendation_calls_provider_in_request_with_only_own_taste(self):
        Preference.objects.create(user=self.other, game=Game.objects.create(title='Private game'), sentiment=-1, reason='Private reason')
        with patch('app.recommendations.ask_provider', return_value=GAMES) as ask:
            response = self.client.post(reverse('recommend'), {'context': 'Something relaxing'}, HTTP_HX_REQUEST='true')
        ask.assert_called_once_with([{'game': 'Portal 2', 'feeling': 'like', 'reason': 'Clever puzzles'}], 'Something relaxing',
                                    owned={'replay': [], 'backlog': [], 'all': [], 'known': ['Portal 2']})
        self.assertContains(response, 'The Talos Principle')
        self.assertContains(response, '/games/search/?query=The%20Talos%20Principle&amp;feeling=dislike')
        self.assertNotContains(response, 'Private game')
        self.assertNotContains(response, 'hx-trigger')
        run = RecommendationRun.objects.get()
        self.assertEqual(run.user_id, self.user.pk)
        self.assertEqual(run.results, GAMES)
        self.assertEqual(run.inputs['request'], 'Something relaxing')

    def test_normal_form_post_also_calls_ai(self):
        with patch('app.recommendations.ask_provider', return_value=GAMES) as ask:
            response = self.client.post(reverse('recommend'), {})
        self.assertRedirects(response, reverse('run', args=[RecommendationRun.objects.get().pk]))
        ask.assert_called_once()

    def test_no_saved_taste_does_not_call_model(self):
        self.client.force_login(self.other)
        with patch('app.recommendations.ask_provider') as ask:
            response = self.client.post(reverse('recommend'), {}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Add a game you like or dislike first')
        ask.assert_not_called()

    def test_failure_shows_error_without_fake_recommendations_or_queued_run(self):
        for htmx in (False, True):
            with self.subTest(htmx=htmx), patch('app.recommendations.ask_provider', side_effect=RecommendationError('AI provider is unavailable')):
                response = self.client.post(reverse('recommend'), {'context': 'Puzzle games'}, HTTP_HX_REQUEST='true' if htmx else 'false')
                self.assertContains(response, 'AI provider is unavailable')
        self.assertFalse(RecommendationRun.objects.exists())
        self.assertTrue(Preference.objects.filter(pk=self.preference.pk).exists())

    def test_account_isolation_and_feedback_prefill(self):
        run = RecommendationRun.objects.create(user=self.other, inputs={}, results=GAMES)
        other_pref = Preference.objects.create(user=self.other, game=Game.objects.create(title='Other'), sentiment=1, reason='Private')
        for url in (reverse('run', args=[run.pk]), reverse('preference_edit', args=[other_pref.pk]),
                    reverse('preference_new') + f'?run={run.pk}&index=0'):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse('preference_delete', args=[other_pref.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('home')), 'Private')
        run.user = self.user
        run.save()
        response = self.client.get(reverse('preference_new'), {'run': run.pk, 'index': 0, 'feeling': 'dislike'})
        self.assertContains(response, 'The Talos Principle')
        self.assertEqual(response.context['form'].initial['sentiment'], -1)
        self.assertEqual(Preference.objects.count(), 2)  # Prefill waits for the user's reason and save.

    def test_invalid_input_and_csrf(self):
        with patch('app.recommendations.ask_provider') as ask:
            response = self.client.post(reverse('recommend'), {'context': 'x' * 1001}, HTTP_HX_REQUEST='true')
        self.assertContains(response, 'under 1,000 characters')
        ask.assert_not_called()
        self.assertEqual(self.client.get(reverse('preference_new'), {'run': 'bad'}).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(reverse('recommend')).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('recommend')).status_code, 302)

    def test_ai_text_is_escaped(self):
        games = [GAMES[0] | {'title': '<script>alert(1)</script>'}]
        with patch('app.recommendations.ask_provider', return_value=games):
            response = self.client.post(reverse('recommend'), {}, HTTP_HX_REQUEST='true')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_openai_compatible_adapter_uses_configured_endpoint(self):
        def respond(request):
            self.assertEqual(str(request.url), 'http://localhost:11434/v1/chat/completions')
            self.assertNotIn('authorization', request.headers)
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'qwen3.5:latest')
            self.assertNotIn('tools', payload)
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertEqual(payload['reasoning_effort'], 'none')
            self.assertEqual(payload['max_tokens'], 1234)
            self.assertIn('Clever puzzles', payload['messages'][1]['content'])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'games': GAMES})}}]})
        taste = [{'game': 'Portal 2', 'feeling': 'like', 'reason': 'Clever puzzles'}]
        with override_settings(OPENAI_COMPATIBLE_BASE_URL='http://localhost:11434/v1', OPENAI_COMPATIBLE_MODEL='qwen3.5:latest', OPENAI_COMPATIBLE_REASONING_EFFORT='none', OPENAI_COMPATIBLE_MAX_TOKENS=1234):
            self.assertEqual(ask_provider(taste, transport=httpx.MockTransport(respond)), [GAMES[0] | {'category': 'discover'}])

    def test_adapter_rejects_invalid_truncated_and_overlong_output(self):
        bodies = [{}, {'choices': []}, {'choices': [{'finish_reason': 'length'}]},
                  {'choices': [{'finish_reason': 'stop', 'message': {'content': 'not json'}}]},
                  {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'games': [GAMES[0] | {'title': 'x' * 201}]})}}]}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(RecommendationError):
                ask_provider([], transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
        with self.assertRaises(RecommendationError):
            ask_provider([], transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        def timeout(request):
            raise httpx.ReadTimeout('timed out')
        with self.assertRaises(RecommendationError):
            ask_provider([], transport=httpx.MockTransport(timeout))

    def test_adapter_omits_duplicates_and_already_known_games(self):
        games = [GAMES[0], GAMES[0], GAMES[0] | {'title': 'Portal 2'}]
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'games': games})}}]}
        result = ask_provider([{'game': 'portal 2'}], transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
        self.assertEqual(result, [GAMES[0] | {'category': 'discover'}])

    def test_owned_picks_are_verified_and_loved_is_a_strong_signal(self):
        account = LinkedAccount.objects.create(user=self.user, provider='steam', external_user_id='123')
        backlog = Game.objects.create(title='Backlog game')
        ignored = Game.objects.create(title='Ignored game')
        Ownership.objects.bulk_create([Ownership(account=account, game=game) for game in (self.game, backlog, ignored)])
        self.preference.sentiment = 2
        self.preference.save()
        Preference.objects.create(user=self.user, game=backlog, sentiment=-2)
        Preference.objects.create(user=self.user, game=ignored, sentiment=0)
        suggestions = [
            {'category': 'discover', 'title': 'Portal 2'},
            {'category': 'replay', 'title': 'Portal 2'},
            {'category': 'backlog', 'title': 'Backlog game'},
            {'category': 'discover', 'title': 'Ignored game'},
            {'category': 'replay', 'title': 'Unknown owned game'},
            {'category': 'discover', 'title': 'New game'},
        ]
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
            'games': [suggestion | {'rationale': 'Fits your taste', 'drawback': 'May be slow'} for suggestion in suggestions],
        })}}]}
        owned = {'replay': ['Portal 2'], 'backlog': ['Backlog game'],
                 'all': ['Backlog game', 'Ignored game', 'Portal 2'],
                 'known': ['Ignored game', 'Backlog game', 'Portal 2']}
        picks = ask_provider([{'game': 'Portal 2', 'feeling': 'loved', 'reason': 'Clever puzzles'}], owned=owned,
                             transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
        self.assertEqual([(pick['category'], pick['title']) for pick in picks],
                         [('replay', 'Portal 2'), ('backlog', 'Backlog game'), ('discover', 'New game')])
        with patch('app.recommendations.ask_provider', return_value=picks) as ask:
            run = create_run(self.user)
        self.assertEqual(ask.call_args.args[0], [{'game': 'Portal 2', 'feeling': 'loved', 'reason': 'Clever puzzles'}])
        self.assertEqual(ask.call_args.kwargs['owned'], owned)
        page = self.client.get(reverse('run', args=[run.pk]))
        self.assertContains(page, 'Favourites to revisit')
        self.assertContains(page, "Games you haven't played yet")
        self.assertContains(page, 'New games to explore')

    def test_recommender_accepts_three_picks_in_each_group(self):
        owned = {
            'replay': [f'Replay {n}' for n in range(3)],
            'backlog': [f'Backlog {n}' for n in range(3)],
            'all': [f'Replay {n}' for n in range(3)] + [f'Backlog {n}' for n in range(3)],
            'known': [],
        }
        games = [
            {'category': category, 'title': f'{name} {n}', 'rationale': 'Good fit', 'drawback': 'Maybe slow'}
            for category, name in (('replay', 'Replay'), ('backlog', 'Backlog'), ('discover', 'New'))
            for n in range(3)
        ]
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'games': games})}}]}
        result = ask_provider([], owned=owned,
                              transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
        self.assertEqual(result, games)
