import json
import tempfile
import time
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import httpx
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.games import save_preference
from app.llm import (Candidate, Discovery, Extraction, OpenAIProvider, ProviderError,
                     RankedGame, Reranking, enhance_run, ground_candidate, validate_reranking)
from app.models import (ConversationTurn, Feedback, Game, GameEvidence, GameMode, OpenIDNonce,
                        OwnershipActivity, PlaytimeSnapshot, Preference, RecommendationRun,
                        ReviewEvidence, SteamAccount)
from app.recommendations import create_run, rank, record_feedback
from app.steam import (OPENID_ENDPOINT, OPENID_NS, OwnedGame, SteamClient, SteamError,
                       UnavailableData, sync_account, sync_reviews, validate_openid)
from app.worker import process_pending, worker_lock

CONTEXT = {'minutes': 30, 'energy': 1, 'platform': 'windows', 'mode': 'solo', 'scope': 'either'}


@override_settings(AI_ENABLED=False, CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class AppTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('alice', password='test-password-9482')
        self.other = get_user_model().objects.create_user('bob', password='test-password-9482')
        self.game = Game.objects.create(title='Test Puzzle', steam_appid=123, platforms=['windows'],
                                       mechanics=['puzzles'], solo=True, session_minutes=15,
                                       attention=1, intensity=1, pause_flexible=True)
        self.second = Game.objects.create(title='Test Epic', steam_appid=456, platforms=['linux'],
                                         solo=True, coop=True, session_minutes=90, attention=3)
        self.client.force_login(self.user)

    def test_signup_onboarding_profile_and_recommendations(self):
        self.client.logout()
        response = self.client.post(reverse('signup'), {'username': 'new-player',
            'password1': 'another-good-password-82', 'password2': 'another-good-password-82'})
        self.assertRedirects(response, reverse('onboarding'))
        response = self.client.post(reverse('onboarding'), {'kind': 'game', 'subject': 'Test Puzzle',
                                  'sentiment': 1, 'reason': 'Thoughtful puzzles'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Preference.objects.get().subject, 'test puzzle')
        response = self.client.post(reverse('recommend'), CONTEXT)
        self.assertRedirects(response, reverse('run', args=[RecommendationRun.objects.get().pk]))
        self.assertContains(self.client.get(response.url), 'Test Puzzle')
        for name in ('profile', 'home', 'history', 'preference_new'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_account_isolation(self):
        pref = save_preference(self.other, {'kind': 'game', 'subject': 'Private preference', 'sentiment': 1})
        run = create_run(self.other, CONTEXT)
        turn = ConversationTurn.objects.create(user=self.other, text='Private conversation')
        for url in (reverse('preference_edit', args=[pref.pk]), reverse('run', args=[run.pk])):
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(url, HTTP_HX_REQUEST='true').status_code, 404)
        for url in (reverse('preference_delete', args=[pref.pk]), reverse('feedback', args=[run.pk]),
                    reverse('confirm_proposal', args=[turn.pk, 0])):
            self.assertEqual(self.client.post(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse('profile')), 'Private preference')
        self.assertNotContains(self.client.get(reverse('profile')), 'Private conversation')
        self.assertTrue(Preference.objects.filter(pk=pref.pk).exists())
        self.assertEqual(self.client.post(reverse('steam_sync')).status_code, 404)

    def test_authenticated_endpoints_and_csrf(self):
        self.client.logout()
        for name in ('profile', 'recommend', 'conversation', 'ownership', 'steam_sync'):
            self.assertEqual(self.client.post(reverse(name)).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        for name in ('recommend', 'conversation', 'steam_start', 'ownership'):
            self.assertEqual(csrf.post(reverse(name)).status_code, 403)
        self.assertEqual(self.client.get(reverse('steam_start')).status_code, 405)

    def test_invalid_session_and_feedback_are_rejected(self):
        self.assertEqual(self.client.post(reverse('recommend'), CONTEXT | {'minutes': -1}).status_code, 400)
        self.assertEqual(RecommendationRun.objects.count(), 0)
        run = create_run(self.user, CONTEXT)
        self.assertEqual(self.client.post(reverse('feedback', args=[run.pk]),
            {'game_id': self.second.pk, 'kind': 'like'}).status_code, 400)
        self.assertEqual(self.client.post(reverse('feedback', args=[run.pk]),
            {'game_id': self.game.pk, 'kind': 'invented'}).status_code, 400)

    def test_hard_constraints_unknowns_and_ownership(self):
        unknown = Game.objects.create(title='Unknown', platforms=[], solo=None)
        run = create_run(self.user, CONTEXT)
        self.assertEqual([r['game_id'] for r in run.results], [self.game.pk])
        self.assertEqual(create_run(self.user, CONTEXT | {'scope': 'owned'}).results, [])
        OwnershipActivity.objects.create(user=self.other, game=self.game, manual_owned=True)
        self.assertEqual(create_run(self.user, CONTEXT | {'scope': 'owned'}).results, [])
        activity = OwnershipActivity.objects.create(user=self.user, game=self.game, steam_owned=True)
        self.assertEqual(len(create_run(self.user, CONTEXT | {'scope': 'owned'}).results), 1)
        self.assertEqual(create_run(self.user, CONTEXT | {'scope': 'new'}).results, [])
        activity.manual_owned = False
        activity.save()
        self.assertEqual(create_run(self.user, CONTEXT | {'scope': 'owned'}).results, [])
        self.assertEqual(create_run(self.user, CONTEXT | {'mode': 'coop'}).results, [])
        self.assertNotIn(unknown.pk, [r['game_id'] for r in run.results])

    def test_mode_specific_suitability(self):
        GameMode.objects.create(game=self.game, name='Co-op puzzle', coop=True, session_minutes=20, attention=1)
        run = create_run(self.user, CONTEXT | {'mode': 'coop'})
        self.assertEqual(run.results[0]['mode'], 'Co-op puzzle')
        self.assertEqual(run.results[0]['facts']['duration'], 'Estimated session: 20 minutes.')

    def test_feedback_lasting_vs_temporary(self):
        run = create_run(self.user, CONTEXT)
        record_feedback(self.user, run, self.game.pk, 'not_tonight')
        self.assertFalse(Preference.objects.exists())
        self.assertEqual(create_run(self.user, CONTEXT).results, [])
        self.assertEqual(len(create_run(self.user, CONTEXT | {'minutes': 45}).results), 1)
        with patch('app.recommendations.timezone.localdate', return_value=timezone.localdate() + timedelta(days=1)):
            self.assertEqual(len(create_run(self.user, CONTEXT).results), 1)
        record_feedback(self.user, run, self.game.pk, 'dislike')
        pref = Preference.objects.get()
        self.assertTrue(pref.explicit)
        self.assertEqual(pref.sentiment, -1)
        self.assertEqual(create_run(self.user, CONTEXT | {'minutes': 45}).results, [])
        self.assertEqual(Feedback.objects.count(), 1)

    def test_explicit_preference_edits_and_manual_ownership(self):
        pref = save_preference(self.user, {'kind': 'game', 'subject': self.game.title, 'sentiment': -1})
        response = self.client.post(reverse('preference_edit', args=[pref.pk]),
            {'kind': 'game', 'subject': self.game.title, 'sentiment': 1, 'reason': 'Changed my mind'})
        self.assertEqual(response.status_code, 302)
        pref.refresh_from_db()
        self.assertEqual(pref.sentiment, 1)
        self.client.post(reverse('ownership'), {'game': self.game.pk, 'owned': 'yes'})
        self.assertTrue(OwnershipActivity.objects.get(user=self.user).owned)

    def test_scores_are_stable_and_variety_is_preserved(self):
        for i in range(8):
            Game.objects.create(title=f'Long {i}', platforms=['windows'], solo=True, session_minutes=90, attention=2)
        first = create_run(self.user, CONTEXT | {'minutes': 120})
        second = create_run(self.user, CONTEXT | {'minutes': 120})
        self.assertEqual(first.scores, second.scores)
        self.assertEqual(len(first.results), 6)
        self.assertEqual(len({r['game_id'] for r in first.results}), 6)
        self.assertTrue({'Quick fun', 'Longer immersion'} <= {r['style'] for r in first.results})
        self.assertEqual(first.inputs['weights']['taste'], .5)

    def test_playtime_is_not_a_taste_preference(self):
        OwnershipActivity.objects.create(user=self.user, game=self.game, total_minutes=9000)
        run = create_run(self.user, CONTEXT)
        self.assertFalse(Preference.objects.exists())
        self.assertEqual(run.scores[0]['components']['taste'], .5)
        self.assertIn('not proof of enjoyment', run.results[0]['facts']['engagement'])

    def test_seed_is_idempotent_and_preserves_edits(self):
        call_command('seed_demo', stdout=StringIO())
        count = Game.objects.count()
        evidence_count = GameEvidence.objects.count()
        game = Game.objects.get(steam_appid=620)
        game.title = 'My title'
        game.save()
        call_command('seed_demo', stdout=StringIO())
        self.assertEqual(Game.objects.count(), count)
        self.assertEqual(GameEvidence.objects.count(), evidence_count)
        game.refresh_from_db()
        self.assertEqual(game.title, 'My title')
        self.assertTrue(game.demonstration)

    def test_conversation_without_credentials_retains_text(self):
        self.client.post(reverse('conversation'), {'text': 'I like puzzling games.'})
        turn = ConversationTurn.objects.get()
        self.assertEqual(turn.status, 'manual')
        self.assertFalse(Preference.objects.exists())

    @override_settings(AI_ENABLED=True)
    def test_conversation_proposal_requires_confirmation_and_handles_failure(self):
        parsed = Extraction.model_validate({'preferences': [{'kind': 'mechanic', 'subject': 'puzzles',
                         'sentiment': 1, 'reason': 'Thoughtful', 'confidence': .7}]})
        with patch('app.llm.OpenAIProvider') as provider:
            provider.return_value.extract_preferences.return_value = parsed
            self.client.post(reverse('conversation'), {'text': 'I like puzzles.'})
        self.assertFalse(Preference.objects.exists())
        turn = ConversationTurn.objects.get()
        self.assertContains(self.client.get(reverse('profile')), 'Confirm preference')
        self.client.post(reverse('confirm_proposal', args=[turn.pk, 0]),
            {'kind': 'mechanic', 'subject': 'puzzles', 'sentiment': 1, 'reason': 'Edited reason'})
        pref = Preference.objects.get()
        self.assertTrue(pref.explicit)
        self.assertEqual(pref.source, 'conversation_confirmed')
        self.assertEqual(pref.reason, 'Edited reason')
        with patch('app.llm.OpenAIProvider', side_effect=ProviderError()):
            self.client.post(reverse('conversation'), {'text': 'Please retain this.'})
        self.assertEqual(ConversationTurn.objects.latest('pk').status, 'failed')

    def test_untrusted_text_is_escaped(self):
        save_preference(self.user, {'kind': 'theme', 'subject': '<script>alert(1)</script>', 'sentiment': 1})
        page = self.client.get(reverse('profile'))
        self.assertNotContains(page, '<script>alert(1)</script>')
        self.assertContains(page, '&lt;script&gt;')

    def test_steam_cache_retry_and_malformed_data(self):
        calls = []
        def transport(request):
            calls.append(request)
            if len(calls) < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={'response': {'game_count': 0}})
        client = SteamClient(api_key='secret', transport=httpx.MockTransport(transport), sleep=lambda _: None)
        self.assertEqual(client.owned_games('76561198000000000'), [])
        self.assertEqual(client.owned_games('76561198000000000'), [])
        self.assertEqual(len(calls), 3)
        with self.assertRaises(UnavailableData):
            client._games({}, 'game_count')
        for data in ({'game_count': 2, 'games': []}, {'game_count': 1, 'games': [{'appid': 'bad'}]},
                     {'game_count': 1, 'games': [{'appid': 1, 'playtime_forever': -1}]}):
            with self.assertRaises(UnavailableData):
                client._games(data, 'game_count')

    def test_steam_timeout_is_bounded_and_sanitised(self):
        def timeout(request):
            raise httpx.ReadTimeout('secret upstream url')
        sleep = Mock()
        client = SteamClient(api_key='secret', transport=httpx.MockTransport(timeout), sleep=sleep)
        with self.assertRaisesMessage(SteamError, 'temporarily unavailable'):
            client.owned_games('76561198000000000')
        self.assertEqual(sleep.call_count, 2)

    def test_import_idempotence_unavailability_and_manual_overrides(self):
        account = SteamAccount.objects.create(user=self.user, steam_id='76561198000000000')
        activity = OwnershipActivity.objects.create(user=self.user, game=self.game, manual_owned=False)
        upstream = Mock()
        upstream.owned_games.return_value = [OwnedGame(appid=123, name='Test Puzzle', playtime_forever=60)]
        upstream.recent_games.return_value = [OwnedGame(appid=123, playtime_2weeks=10)]
        self.assertTrue(sync_account(account, upstream))
        self.assertTrue(sync_account(account, upstream))
        self.assertEqual(PlaytimeSnapshot.objects.count(), 1)
        activity.refresh_from_db()
        self.assertFalse(activity.owned)
        self.assertTrue(activity.steam_owned)
        self.assertEqual(activity.recent_minutes, 10)
        upstream.owned_games.side_effect = UnavailableData('private')
        self.assertFalse(sync_account(account, upstream))
        activity.refresh_from_db()
        self.assertTrue(activity.steam_owned)
        self.assertEqual(activity.total_minutes, 60)
        account.refresh_from_db()
        self.assertEqual(account.status, 'unavailable')
        self.assertFalse(Preference.objects.exists())

    def test_partial_recent_and_valid_empty_owned_response(self):
        account = SteamAccount.objects.create(user=self.user, steam_id='76561198000000000')
        upstream = Mock()
        upstream.owned_games.return_value = [OwnedGame(appid=123, playtime_forever=20)]
        upstream.recent_games.side_effect = SteamError('unavailable')
        sync_account(account, upstream)
        account.refresh_from_db()
        self.assertEqual(account.status, 'partial')
        upstream.owned_games.return_value = []
        sync_account(account, upstream)
        self.assertFalse(OwnershipActivity.objects.get().steam_owned)
        self.assertEqual(PlaytimeSnapshot.objects.count(), 1)

    def test_review_import_deduplicates_and_rejects_bad_response(self):
        client = Mock()
        client.reviews.return_value = [{'recommendationid': '101', 'review': '<script>Untrusted</script>', 'voted_up': True}]
        sync_reviews(self.game, client)
        sync_reviews(self.game, client)
        self.assertEqual(ReviewEvidence.objects.count(), 1)
        client.reviews.return_value = [{'recommendationid': '102', 'review': 'bad', 'voted_up': 'true'}]
        with self.assertRaises(SteamError):
            sync_reviews(self.game, client)
        self.assertEqual(ReviewEvidence.objects.count(), 1)

    def openid_assertion(self):
        expected = {'state': 'random-state', 'return_to': 'http://localhost:8000/steam/callback/?state=random-state',
                    'expires': time.time() + 600, 'user_id': self.user.pk}
        params = {'state': expected['state'], 'openid.ns': OPENID_NS, 'openid.mode': 'id_res',
                  'openid.return_to': expected['return_to'], 'openid.op_endpoint': OPENID_ENDPOINT,
                  'openid.identity': 'https://steamcommunity.com/openid/id/76561198000000000',
                  'openid.claimed_id': 'https://steamcommunity.com/openid/id/76561198000000000',
                  'openid.response_nonce': timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ') + 'nonce',
                  'openid.assoc_handle': 'handle', 'openid.sig': 'signature',
                  'openid.signed': 'op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle'}
        return params, expected

    def test_openid_server_validation_and_tamper_rejection(self):
        params, expected = self.openid_assertion()
        requests = []
        def valid(request):
            requests.append(request)
            self.assertIn(b'openid.mode=check_authentication', request.content)
            return httpx.Response(200, text='ns:' + OPENID_NS + '\nis_valid:true\n')
        transport = httpx.MockTransport(valid)
        self.assertEqual(validate_openid(params, expected, transport)[0], '76561198000000000')
        for field, value in [('state', 'evil'), ('openid.return_to', 'https://evil.example'),
                             ('openid.op_endpoint', 'http://127.0.0.1'), ('openid.signed', 'identity'),
                             ('openid.identity', 'https://evil.example'), ('openid.mode', 'cancel'),
                             ('openid.response_nonce', '2020-01-01T00:00:00Zold')]:
            with self.assertRaises(SteamError):
                validate_openid(params | {field: value}, expected, transport)
        self.assertEqual(len(requests), 1)
        with self.assertRaises(SteamError):
            validate_openid(params, expected, httpx.MockTransport(lambda _: httpx.Response(200, text='is_valid:false')))
        OpenIDNonce.objects.create(value=params['openid.response_nonce'])
        with self.assertRaises(SteamError):
            validate_openid(params, expected, transport)

    def test_openid_callback_links_but_does_not_import_and_rejects_replay(self):
        params, expected = self.openid_assertion()
        session = self.client.session
        session['steam_openid'] = expected
        session.save()
        with patch('app.steam.validate_openid', return_value=('76561198000000000', params['openid.response_nonce'])), patch('app.steam.sync_account') as sync:
            response = self.client.get(reverse('steam_callback'), params)
            self.assertEqual(response.status_code, 302)
            self.assertFalse(sync.called)
            self.assertTrue(SteamAccount.objects.get(user=self.user).sync_requested)
            self.assertEqual(self.client.get(reverse('steam_callback'), params).status_code, 400)
        self.assertEqual(OpenIDNonce.objects.count(), 1)

    def test_steam_start_uses_configured_origin_and_session(self):
        response = self.client.post(reverse('steam_start'))
        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query['openid.realm'], ['http://localhost:8000/'])
        self.assertIn('steam_openid', self.client.session)

    def test_openid_cannot_link_another_users_account(self):
        SteamAccount.objects.create(user=self.other, steam_id='76561198000000000')
        params, expected = self.openid_assertion()
        session = self.client.session
        session['steam_openid'] = expected
        session.save()
        with patch('app.steam.validate_openid', return_value=('76561198000000000', params['openid.response_nonce'])):
            self.assertEqual(self.client.get(reverse('steam_callback'), params).status_code, 400)
        self.assertEqual(SteamAccount.objects.get().user_id, self.other.pk)

    def store_payload(self, appid=789, title='New Verified Game'):
        return {str(appid): {'success': True, 'data': {'steam_appid': appid, 'name': title, 'type': 'game',
                    'platforms': {'windows': True, 'mac': False, 'linux': False},
                    'categories': [{'id': 2}, {'id': 1}]}}}

    def test_grounding_adds_real_candidates_but_not_invented_facts(self):
        client = Mock()
        client.get_json.return_value = self.store_payload()
        game = ground_candidate(Candidate(appid=789, title='New Verified Game'), client)
        self.assertEqual(game.platforms, ['windows'])
        self.assertTrue(game.solo)
        self.assertIsNone(game.competitive)  # Generic multiplayer is not PvP evidence.
        self.assertIsNone(game.session_minutes)
        self.assertEqual(game.evidence.filter(verified=True).count(), 4)
        ground_candidate(Candidate(appid=789, title=game.title), client)
        self.assertEqual(client.get_json.call_count, 1)
        with self.assertRaises(ProviderError):
            ground_candidate(Candidate(appid=789, title='Fabricated title'), client)
        client.get_json.return_value = {'999': {'success': False}}
        with self.assertRaises(ProviderError):
            ground_candidate(Candidate(appid=999, title='Missing'), client)

    def test_rerank_rejects_unknown_duplicate_missing_ids_and_evidence(self):
        run = create_run(self.user, CONTEXT)
        row = {'game_id': self.game.pk, 'fact_keys': ['platform']}
        self.assertEqual(validate_reranking({'games': [row]}, run.scores)[0]['rationale'], 'Listed for Windows.')
        for data in ({'games': [row | {'game_id': 999999}]}, {'games': [row, row]}, {'games': []},
                     {'games': [row | {'fact_keys': ['invented']}]}, {'games': [row | {'extra': 'unsafe'}]}):
            with self.assertRaises(ProviderError):
                validate_reranking(data, run.scores)

    def test_openai_adapter_rejects_incomplete_and_malformed_outputs(self):
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(status='incomplete', output_parsed=None)
        with self.assertRaises(ProviderError):
            OpenAIProvider(client=client).extract_preferences(['text'])
        client.responses.parse.return_value = SimpleNamespace(status='completed', output_parsed=SimpleNamespace(model_dump=lambda: {'preferences': 'bad'}))
        with self.assertRaises(ProviderError):
            OpenAIProvider(client=client).extract_preferences(['text'])
        self.assertFalse(client.responses.parse.call_args.kwargs['store'])

    def test_enhancement_discovers_beyond_catalogue_and_saves_evidence(self):
        run = create_run(self.user, CONTEXT)
        provider, steam = Mock(), Mock()
        provider.sources = ['https://store.steampowered.com/app/789/']
        provider.discover_candidates.return_value = Discovery(candidates=[Candidate(appid=789, title='New Verified Game')])
        provider.rerank.side_effect = lambda evidence: Reranking(games=[RankedGame(game_id=c['game_id'], fact_keys=['platform']) for c in evidence['candidates']])
        steam.get_json.return_value = self.store_payload()
        enhance_run(run, provider, steam)
        run.refresh_from_db()
        self.assertEqual(run.status, 'enhanced')
        self.assertIn('New Verified Game', [r['title'] for r in run.results])
        self.assertIn('rerank_input', run.diagnostics)
        self.assertNotIn('user_id', json.dumps(run.diagnostics['discovery_input']))

    def test_enhancement_fallback_preserves_deterministic_results(self):
        run = create_run(self.user, CONTEXT)
        original = run.results
        provider = Mock()
        provider.discover_candidates.side_effect = ProviderError('not available')
        enhance_run(run, provider)
        run.refresh_from_db()
        self.assertEqual(run.status, 'fallback')
        self.assertEqual(run.results, original)
        self.assertEqual(run.deterministic_results, original)

    def test_worker_missing_credentials_falls_back_and_recovers_expired_lease(self):
        run = create_run(self.user, CONTEXT)
        run.status = 'processing'
        run.lease_until = timezone.now() - timedelta(minutes=1)
        run.save()
        with override_settings(OPENAI_API_KEY=''):
            self.assertEqual(process_pending(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, 'fallback')
        self.assertIsNone(run.lease_until)

    def test_worker_lock_prevents_overlapping_imports(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory, override_settings(PRIVATE_CACHE_DIR=Path(directory)):
            with worker_lock():
                with self.assertRaises(CommandError):
                    with worker_lock():
                        self.fail('Concurrent worker acquired the lock')

    def test_fallback_does_not_resurrect_changed_ownership(self):
        activity = OwnershipActivity.objects.create(user=self.user, game=self.game, manual_owned=True)
        run = create_run(self.user, CONTEXT | {'scope': 'owned'})
        original = run.deterministic_results
        provider = Mock()
        def fail_after_change(_):
            activity.manual_owned = False
            activity.save()
            raise ProviderError('Provider failed after ownership changed')
        provider.discover_candidates.side_effect = fail_after_change
        enhance_run(run, provider)
        self.assertEqual(run.results, [])
        self.assertEqual(run.deterministic_results, original)
        self.assertEqual(run.status, 'fallback')

    def test_rerank_explanations_use_current_ownership_evidence(self):
        run = create_run(self.user, CONTEXT)
        provider = Mock()
        provider.sources = []
        provider.discover_candidates.return_value = Discovery(candidates=[])
        def update_ownership(_):
            OwnershipActivity.objects.create(user=self.user, game=self.game, manual_owned=True)
            return Reranking(games=[RankedGame(game_id=self.game.pk, fact_keys=['ownership'])])
        provider.rerank.side_effect = update_ownership
        enhance_run(run, provider)
        self.assertEqual(run.status, 'enhanced')
        self.assertEqual(run.results[0]['rationale'], 'In your library.')

    def test_owned_only_discovery_rejects_external_games_before_fetch(self):
        OwnershipActivity.objects.create(user=self.user, game=self.game, manual_owned=True)
        run = create_run(self.user, CONTEXT | {'scope': 'owned'})
        provider, steam = Mock(), Mock()
        provider.sources = []
        provider.discover_candidates.return_value = Discovery(candidates=[Candidate(appid=999, title='Unowned')])
        provider.rerank.return_value = Reranking(games=[RankedGame(game_id=self.game.pk, fact_keys=['ownership'])])
        enhance_run(run, provider, steam)
        self.assertFalse(steam.get_json.called)
        self.assertEqual(run.diagnostics['rejected_appids'], [999])
        self.assertEqual([r['game_id'] for r in run.results], [self.game.pk])

    def test_grounding_refreshes_expired_evidence(self):
        client = Mock()
        client.get_json.return_value = self.store_payload()
        game = ground_candidate(Candidate(appid=789, title='New Verified Game'), client)
        game.evidence.update(observed_at=timezone.now() - timedelta(days=2))
        ground_candidate(Candidate(appid=789, title='New Verified Game'), client)
        self.assertEqual(client.get_json.call_count, 2)
        self.assertEqual(game.evidence.count(), 4)
        self.assertTrue(all(e.observed_at > timezone.now() - timedelta(minutes=1) for e in game.evidence.all()))

    def test_invalid_reranking_and_bad_json_produce_fallback(self):
        run = create_run(self.user, CONTEXT)
        provider = Mock()
        provider.sources = []
        provider.discover_candidates.return_value = Discovery(candidates=[])
        provider.rerank.return_value = Reranking(games=[RankedGame(game_id=99999, fact_keys=['platform'])])
        enhance_run(run, provider)
        self.assertEqual(run.status, 'fallback')
        self.assertEqual(run.results, run.deterministic_results)
        client = Mock()
        client.responses.parse.side_effect = json.JSONDecodeError('broken', '{', 0)
        with self.assertRaises(ProviderError):
            OpenAIProvider(client=client).extract_preferences(['user text'])
