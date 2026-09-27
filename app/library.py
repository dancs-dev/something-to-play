"""Provider-neutral ownership and catalogue persistence."""
from django.db import transaction
from django.utils import timezone

from .models import CatalogueState, Game, GameIdentity, Ownership
from . import steam


PROVIDERS = {'steam': steam}


def game_for_identity(provider, external_id, title):
    identity = GameIdentity.objects.filter(provider=provider, external_id=external_id).select_related('game').first()
    if identity:
        return identity.game
    game = Game.objects.create(title=title)
    GameIdentity.objects.create(game=game, provider=provider, external_id=external_id)
    return game


def sync_account(account):
    games = PROVIDERS[account.provider].owned_games(account.external_user_id)
    if not games and Ownership.objects.filter(account=account, is_active=True).exists():
        # ponytail: Steam's empty response cannot prove visibility; require a nonempty library before deactivating all games.
        raise steam.SteamError('Steam returned an empty library. Check game details visibility; existing ownership was kept.')
    with transaction.atomic():
        owned_ids = []
        for external_id, title in games.items():
            game = game_for_identity(account.provider, external_id, title)
            ownership, _ = Ownership.objects.get_or_create(account=account, game=game)
            if not ownership.is_active:
                ownership.is_active = True
                ownership.save(update_fields=['is_active'])
            owned_ids.append(game.pk)
        Ownership.objects.filter(account=account, is_active=True).exclude(game_id__in=owned_ids).update(is_active=False)
        account.last_synced_at = timezone.now()
        account.save(update_fields=['last_synced_at'])
    return len(games)


def refresh_catalogue(provider='steam'):
    # Each completed page is retained. A failed refresh leaves its completion marker unchanged.
    state = CatalogueState.objects.filter(provider=provider).first()
    started_at = timezone.now()
    since = max(0, int(state.last_synced_at.timestamp()) - 1) if state else None
    for page in PROVIDERS[provider].catalogue_pages(modified_since=since):
        with transaction.atomic():
            known = set(GameIdentity.objects.filter(provider=provider, external_id__in=page).values_list('external_id', flat=True))
            new = [(external_id, title) for external_id, title in page.items() if external_id not in known]
            games = Game.objects.bulk_create([Game(title=title) for _, title in new])
            GameIdentity.objects.bulk_create([
                GameIdentity(game=game, provider=provider, external_id=external_id)
                for (external_id, _), game in zip(new, games)
            ])
    CatalogueState.objects.update_or_create(provider=provider, defaults={'last_synced_at': started_at})
