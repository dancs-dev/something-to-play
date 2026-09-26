from .models import Game, Preference


def save_preference(user, data, source='manual'):
    subject = data['subject'].strip().casefold()
    game = Game.objects.filter(title__iexact=subject).first() if data['kind'] == 'game' else None
    preference, _ = Preference.objects.update_or_create(
        user=user, kind=data['kind'], subject=subject,
        defaults={'game': game, 'sentiment': data['sentiment'], 'reason': data.get('reason', ''),
                  'source': source, 'confidence': 1, 'explicit': True},
    )
    return preference
