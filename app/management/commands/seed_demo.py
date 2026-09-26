from django.core.management.base import BaseCommand
from django.db import transaction
from app.models import Game, GameEvidence

# Curated demonstration estimates, not Steam metadata or fabricated reviews.
# appid, title, platforms, mechanics, themes, minutes, attention, intensity, pause, learning, solo, coop, competitive
DEMO = [
 (413150, 'Stardew Valley', 'windows macos linux', 'farming crafting', 'cozy rural', 30, 1, 1, True, 1, True, True, False),
 (620, 'Portal 2', 'windows macos linux', 'puzzles exploration', 'science-fiction comedy', 30, 2, 1, True, 1, True, True, False),
 (1145360, 'Hades', 'windows macos', 'action roguelike', 'mythology', 35, 3, 3, True, 2, True, False, False),
 (646570, 'Slay the Spire', 'windows macos linux', 'cards roguelike strategy', 'fantasy', 45, 2, 1, True, 2, True, False, False),
 (105600, 'Terraria', 'windows macos linux', 'crafting exploration survival', 'fantasy', 60, 2, 2, True, 2, True, True, False),
 (1086940, "Baldur's Gate 3", 'windows macos', 'roleplaying strategy exploration', 'fantasy narrative', 90, 3, 2, True, 3, True, True, False),
 (367520, 'Hollow Knight', 'windows macos linux', 'action exploration platforming', 'fantasy', 60, 3, 3, False, 2, True, False, False),
 (250900, 'The Binding of Isaac: Rebirth', 'windows macos linux', 'action roguelike', 'horror', 40, 3, 3, True, 2, True, True, False),
 (1794680, 'Vampire Survivors', 'windows macos', 'action roguelike', 'fantasy', 30, 2, 2, True, 1, True, True, False),
 (588650, 'Dead Cells', 'windows macos linux', 'action roguelike platforming', 'fantasy', 40, 3, 3, True, 2, True, False, False),
 (632360, 'Risk of Rain 2', 'windows', 'action roguelike', 'science-fiction', 60, 3, 3, False, 2, True, True, False),
 (548430, 'Deep Rock Galactic', 'windows', 'action exploration', 'science-fiction', 35, 2, 3, False, 2, True, True, False),
 (594570, 'Total War: WARHAMMER II', 'windows macos linux', 'strategy management', 'fantasy', 90, 3, 2, True, 3, True, True, True),
 (289070, 'Sid Meier’s Civilization VI', 'windows macos linux', 'strategy management', 'history', 90, 3, 1, True, 3, True, True, True),
 (427520, 'Factorio', 'windows macos linux', 'automation crafting management', 'science-fiction', 90, 3, 1, True, 3, True, True, False),
 (1195290, 'Monument Valley: Panoramic Edition', 'windows', 'puzzles', 'surreal', 15, 1, 1, True, 1, True, False, False),
 (638970, 'Yakuza 0', 'windows', 'action roleplaying', 'crime narrative', 60, 2, 2, True, 2, True, False, False),
 (960090, 'Bloons TD 6', 'windows macos', 'strategy', 'colorful', 20, 2, 1, True, 1, True, True, False),
 (945360, 'Among Us', 'windows', 'deduction social', 'science-fiction', 15, 2, 2, False, 1, False, True, True),
 (504230, 'Celeste', 'windows macos linux', 'platforming', 'narrative', 20, 3, 3, True, 2, True, False, False),
]


class Command(BaseCommand):
    help = 'Seed labelled demonstration games without overwriting existing records.'

    @transaction.atomic
    def handle(self, *args, **options):
        added = 0
        for appid, title, platforms, mechanics, themes, minutes, attention, intensity, pause, learning, solo, coop, competitive in DEMO:
            fields = dict(title=title, platforms=platforms.split(), mechanics=mechanics.split(), themes=themes.split(),
                          session_minutes=minutes, attention=attention, intensity=intensity, pause_flexible=pause,
                          learning_effort=learning, solo=solo, coop=coop, competitive=competitive,
                          demonstration=True, source_url=f'https://store.steampowered.com/app/{appid}/')
            game, created = Game.objects.get_or_create(steam_appid=appid, defaults=fields)
            if created:
                added += 1
                GameEvidence.objects.bulk_create([GameEvidence(game=game, attribute=key, value=value,
                    source='demonstration', confidence=.5, verified=False,
                    excerpt='Curated demonstration estimate; verify before relying on it.')
                    for key, value in fields.items() if key not in ('title', 'demonstration', 'source_url')])
        self.stdout.write(f'Added {added} demonstration games. Estimates are not verified catalogue facts.')
