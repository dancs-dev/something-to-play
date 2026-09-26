from django.core.management.base import BaseCommand, CommandError
from app.models import Game
from app.steam import SteamError, sync_reviews
from app.worker import worker_lock


class Command(BaseCommand):
    help = 'Import up to 20 English community reviews for one Steam app.'

    def add_arguments(self, parser):
        parser.add_argument('appid', type=int)

    def handle(self, *args, **options):
        game = Game.objects.filter(steam_appid=options['appid']).first()
        if not game:
            raise CommandError('Import or seed this game first.')
        try:
            with worker_lock():
                self.stdout.write(f'Imported {sync_reviews(game)} reviews.')
        except SteamError as exc:
            raise CommandError(str(exc)) from None
