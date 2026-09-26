"""Steam identity and imports. Only OpenID validation runs in a web request."""
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import Game, OpenIDNonce, OwnershipActivity, PlaytimeSnapshot, ReviewEvidence, SteamAccount

OPENID_ENDPOINT = 'https://steamcommunity.com/openid/login'
OPENID_NS = 'http://specs.openid.net/auth/2.0'
TIMEOUT = httpx.Timeout(10, connect=5)


class SteamError(Exception):
    """Sanitised integration failure; never exposes credentials or upstream text."""


class UnavailableData(SteamError):
    pass


class OwnedGame(BaseModel):
    model_config = ConfigDict(strict=True)
    appid: int = Field(gt=0)
    name: str = Field(default='', max_length=200)
    playtime_forever: int | None = Field(default=None, ge=0)
    playtime_2weeks: int | None = Field(default=None, ge=0)


class SteamClient:
    def __init__(self, api_key=None, transport=None, sleep=time.sleep):
        self.api_key = settings.STEAM_API_KEY if api_key is None else api_key
        self.transport = transport
        self.sleep = sleep

    def get_json(self, url, params, ttl=900, force=False):
        key = 'steam:' + hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
        cached = cache.get(key)
        if cached is not None and not force:
            return cached
        with httpx.Client(timeout=TIMEOUT, transport=self.transport, follow_redirects=False) as client:
            for attempt in range(3):
                delay = 2 ** attempt
                try:
                    response = client.get(url, params=params)
                    if response.status_code == 429 or response.status_code >= 500:
                        retry_after = response.headers.get('Retry-After', '')
                        if retry_after.isdigit():
                            delay = min(10, max(delay, int(retry_after)))
                        raise httpx.TransportError('Transient upstream failure')
                    response.raise_for_status()
                    if len(response.content) > 5_000_000:
                        raise SteamError('Steam response exceeded the size limit.')
                    data = response.json()
                    if not isinstance(data, dict):
                        raise SteamError('Steam returned an unexpected response.')
                    cache.set(key, data, ttl)
                    return data
                except httpx.TransportError:
                    if attempt == 2:
                        raise SteamError('Steam is temporarily unavailable.') from None
                    self.sleep(delay)
                except (httpx.HTTPStatusError, ValueError):
                    raise SteamError('Steam returned an invalid response or rejected the request.') from None

    def _player(self, method, steam_id, force=False):
        if not self.api_key:
            raise SteamError('Steam imports need STEAM_API_KEY. Manual ownership remains available.')
        return self.get_json(f'https://api.steampowered.com/IPlayerService/{method}/v1/',
                             {'key': self.api_key, 'steamid': steam_id, 'include_appinfo': 1,
                              'include_played_free_games': 1}, force=force)

    def owned_games(self, steam_id, force=False):
        data = self._player('GetOwnedGames', steam_id, force).get('response')
        return self._games(data, 'game_count')

    def recent_games(self, steam_id, force=False):
        data = self._player('GetRecentlyPlayedGames', steam_id, force).get('response')
        return self._games(data, 'total_count')

    @staticmethod
    def _games(data, count_key):
        if not isinstance(data, dict) or count_key not in data:
            raise UnavailableData('Steam game data is private or unavailable; previous data was kept.')
        count = data[count_key]
        raw = data.get('games', [])
        if type(count) is not int or count < 0 or not isinstance(raw, list) or len(raw) != count:
            raise UnavailableData('Steam returned incomplete game data; previous data was kept.')
        try:
            games = [OwnedGame.model_validate(row) for row in raw]
        except ValidationError:
            raise UnavailableData('Steam returned malformed game data; previous data was kept.') from None
        if len({g.appid for g in games}) != len(games):
            raise UnavailableData('Steam returned duplicate game data; previous data was kept.')
        return games

    def reviews(self, appid):
        data = self.get_json(f'https://store.steampowered.com/appreviews/{int(appid)}',
            {'json': 1, 'filter': 'recent', 'language': 'english', 'num_per_page': 20,
             'purchase_type': 'all', 'review_type': 'all'}, ttl=86400)
        if data.get('success') != 1 or not isinstance(data.get('reviews'), list):
            raise SteamError('Community reviews are unavailable.')
        return data['reviews'][:20]


def sync_account(account, client=None, force=False):
    client = client or SteamClient()
    now = timezone.now()
    SteamAccount.objects.filter(pk=account.pk).update(sync_requested=False, status='syncing', last_attempt_at=now, error='')
    try:
        owned = client.owned_games(account.steam_id, force)
        try:
            recent = {g.appid: g for g in client.recent_games(account.steam_id, force)}
        except SteamError:
            recent = None
        # Network is finished before any database transaction begins.
        for start in range(0, len(owned), 100):
            with transaction.atomic():
                for imported in owned[start:start + 100]:
                    game, _ = Game.objects.get_or_create(steam_appid=imported.appid,
                        defaults={'title': imported.name or f'Steam app {imported.appid}',
                                  'source_url': f'https://store.steampowered.com/app/{imported.appid}/'})
                    activity, _ = OwnershipActivity.objects.get_or_create(user=account.user, game=game)
                    recent_minutes = (recent[imported.appid].playtime_2weeks if imported.appid in recent else 0) if recent is not None else None
                    changed = activity.total_minutes != imported.playtime_forever or activity.recent_minutes != recent_minutes
                    activity.steam_owned = True
                    activity.observed_at = now
                    activity.total_minutes = imported.playtime_forever
                    activity.recent_minutes = recent_minutes
                    activity.save()
                    if imported.playtime_forever is not None and (changed or not activity.snapshots.exists()):
                        PlaytimeSnapshot.objects.get_or_create(activity=activity, observed_at=now,
                            defaults={'total_minutes': imported.playtime_forever, 'recent_minutes': recent_minutes})
        # Only a complete owned-games response can revoke old Steam ownership.
        OwnershipActivity.objects.filter(user=account.user, steam_owned=True).exclude(
            game__steam_appid__in=[g.appid for g in owned]).update(steam_owned=False)
        SteamAccount.objects.filter(pk=account.pk).update(
            status='synced' if recent is not None else 'partial', last_synced_at=now,
            error='' if recent is not None else 'Owned games imported; recent activity unavailable.')
    except SteamError as exc:
        SteamAccount.objects.filter(pk=account.pk).update(
            status='unavailable' if isinstance(exc, UnavailableData) else 'failed', error=str(exc))
        return False
    return True


def sync_reviews(game, client=None):
    if not game.steam_appid:
        return 0
    reviews = (client or SteamClient()).reviews(game.steam_appid)
    # Validate the whole sample before writing it; review text is untrusted data.
    validated = []
    for review in reviews:
        if (not isinstance(review, dict) or not str(review.get('recommendationid', '')).isdigit()
                or len(str(review.get('recommendationid', ''))) > 40
                or not isinstance(review.get('review'), str) or type(review.get('voted_up')) is not bool):
            raise SteamError('Community review response was malformed.')
        validated.append(review)
    with transaction.atomic():
        for review in validated:
            ReviewEvidence.objects.update_or_create(external_id=str(review['recommendationid']),
                defaults={'game': game, 'excerpt': review['review'][:2000], 'positive': review['voted_up'],
                          'source_url': f'https://steamcommunity.com/app/{game.steam_appid}/reviews/'})
    return len(validated)


@require_POST
def start(request):
    state = secrets.token_urlsafe(32)
    return_to = settings.SITE_ORIGIN + '/steam/callback/?' + urlencode({'state': state})
    request.session['steam_openid'] = {'state': state, 'return_to': return_to,
        'expires': time.time() + 600, 'user_id': request.user.pk if request.user.is_authenticated else None}
    params = {'openid.ns': OPENID_NS, 'openid.mode': 'checkid_setup',
              'openid.return_to': return_to, 'openid.realm': settings.SITE_ORIGIN + '/',
              'openid.identity': OPENID_NS + '/identifier_select',
              'openid.claimed_id': OPENID_NS + '/identifier_select'}
    return redirect(OPENID_ENDPOINT + '?' + urlencode(params))


def validate_openid(params, expected, transport=None):
    """Validate a fixed-provider assertion, including server verification and replay age."""
    def reject():
        raise SteamError('Steam sign-in could not be verified. Please try again.')
    if not expected or expected['expires'] < time.time():
        reject()
    if params.get('state') != expected['state'] or params.get('openid.return_to') != expected['return_to']:
        reject()
    if params.get('openid.ns') != OPENID_NS or params.get('openid.mode') != 'id_res':
        reject()
    if params.get('openid.op_endpoint') not in (OPENID_ENDPOINT, 'https://steamcommunity.com/openid/'):
        reject()
    claimed = params.get('openid.claimed_id', '')
    match = re.fullmatch(r'https?://steamcommunity\.com/openid/id/([0-9]{17})', claimed)
    if not match or params.get('openid.identity') != claimed:
        reject()
    signed = set(params.get('openid.signed', '').split(','))
    if not {'op_endpoint', 'claimed_id', 'identity', 'return_to', 'response_nonce', 'assoc_handle'} <= signed:
        reject()
    if not params.get('openid.sig') or not params.get('openid.assoc_handle'):
        reject()
    nonce = params.get('openid.response_nonce', '')
    try:
        timestamp = datetime.strptime(nonce[:20], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt_timezone.utc)
        if not 20 < len(nonce) <= 255 or abs((timezone.now() - timestamp).total_seconds()) > 300:
            reject()
    except ValueError:
        reject()
    if OpenIDNonce.objects.filter(value=nonce).exists():
        reject()
    payload = {k: v for k, v in params.items() if k.startswith('openid.')}
    payload['openid.mode'] = 'check_authentication'
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False) as client:
            response = client.post(OPENID_ENDPOINT, data=payload)
        response.raise_for_status()
        lines = response.text.splitlines()
        if lines.count('is_valid:true') != 1 or any(line == 'is_valid:false' for line in lines):
            reject()
    except httpx.HTTPError:
        reject()
    return match.group(1), nonce


@require_GET
def callback(request):
    expected = request.session.pop('steam_openid', None)
    try:
        if any(len(request.GET.getlist(key)) != 1 for key in request.GET):
            raise SteamError('Duplicate sign-in parameters.')
        if expected and expected['user_id'] != (request.user.pk if request.user.is_authenticated else None):
            raise SteamError('Your login changed during Steam sign-in. Please try again.')
        steam_id, nonce = validate_openid(request.GET.dict(), expected)
        with transaction.atomic():
            OpenIDNonce.objects.create(value=nonce)
            account = SteamAccount.objects.select_related('user').filter(steam_id=steam_id).first()
            if request.user.is_authenticated:
                if account and account.user_id != request.user.pk:
                    raise SteamError('That Steam account is already linked to another account.')
                existing = SteamAccount.objects.filter(user=request.user).first()
                if existing and existing.steam_id != steam_id:
                    raise SteamError('Your account already has a different Steam account linked.')
                if not account:
                    account = SteamAccount.objects.create(user=request.user, steam_id=steam_id)
            elif not account or not account.user.is_active:
                raise SteamError('Create a local account and connect Steam from your profile first.')
        if not request.user.is_authenticated:
            login(request, account.user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, 'Steam identity verified. Your library sync is queued.')
        return redirect('profile')
    except (SteamError, IntegrityError):
        return HttpResponseBadRequest('Steam sign-in failed. Check the account link and try again from your profile or login page.')
