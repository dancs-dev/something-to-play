from django.core.management.base import BaseCommand, CommandError
from app.llm import Candidate, ProviderError, ground_candidate
from app.models import Game
from app.steam import SteamError
from app.worker import worker_lock


class Command(BaseCommand):
    help = 'Verify platform and play-mode metadata for one already imported Steam game (no LLM key needed).'

    def add_arguments(self, parser):
        parser.add_argument('appid', type=int)

    def handle(self, *args, **options):
        game = Game.objects.filter(steam_appid=options['appid']).first()
        if not game:
            raise CommandError('Import or seed this game first.')
        try:
            with worker_lock():
                ground_candidate(Candidate(appid=game.steam_appid, title=game.title))
            self.stdout.write('Store identity and available compatibility metadata verified.')
        except (ProviderError, SteamError) as exc:
            raise CommandError(str(exc)) from None
