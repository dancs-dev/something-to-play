"""Inspectable ranking shared by views and the single worker."""
import hashlib
import json
from math import log1p

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Feedback, Game, OwnershipActivity, Preference, RecommendationRun

SCORING_VERSION = 'scoring-v1'


def snapshot_inputs(user, context):
    preferences = list(Preference.objects.filter(user=user).order_by('-explicit', '-updated_at').values(
        'game_id', 'kind', 'subject', 'sentiment', 'reason', 'source', 'confidence', 'explicit', 'updated_at'))
    for preference in preferences:
        preference['updated_at'] = preference['updated_at'].isoformat()
    context_key = hashlib.sha256(json.dumps([str(timezone.localdate()), context], sort_keys=True).encode()).hexdigest()
    return {'session': context, 'preferences': preferences, 'context_key': context_key,
            'weights': settings.SCORE_WEIGHTS.copy()}


def rank(user, inputs):
    context = inputs['session']
    games = list(Game.objects.prefetch_related('modes', 'evidence').all())
    by_id = {g.pk: g for g in games}
    by_title = {g.title.casefold(): g for g in games}
    activity = {a.game_id: a for a in OwnershipActivity.objects.filter(user=user)}
    disliked = list(Preference.objects.filter(user=user, kind='game', sentiment=-1, explicit=True))
    banned = {p.game_id for p in disliked}
    banned_titles = {p.subject.casefold() for p in disliked}
    suppressed = set(Feedback.objects.filter(
        user=user, kind='not_tonight', run__inputs__context_key=inputs['context_key']).values_list('game_id', flat=True))
    liked = set(Feedback.objects.filter(user=user, kind='like').values_list('game_id', flat=True))
    scores = []
    excluded = {'platform': 0, 'ownership': 0, 'mode': 0, 'feedback': 0}
    for game in games:
        if game.pk in banned | suppressed or game.title.casefold() in banned_titles:
            excluded['feedback'] += 1
            continue
        if context['platform'] not in game.platforms:
            excluded['platform'] += 1
            continue
        act = activity.get(game.pk)
        owned = bool(act and act.owned)
        if (context['scope'] == 'owned' and not owned) or (context['scope'] == 'new' and owned):
            excluded['ownership'] += 1
            continue
        variants = [game, *game.modes.all()]
        if context['mode'] != 'any':
            variants = [v for v in variants if getattr(v, context['mode']) is True]
        if not variants:
            excluded['mode'] += 1
            continue
        taste_signals = []
        matched = []
        for pref in inputs['preferences']:
            strength = 0
            if pref['kind'] == 'game':
                target = by_id.get(pref['game_id']) or by_title.get(pref['subject'].casefold())
                if target and target.pk == game.pk:
                    strength = 1
                elif target:
                    tags = set(target.mechanics + target.themes)
                    strength = len(tags & set(game.mechanics + game.themes)) / max(1, len(tags)) * .7
            elif pref['subject'].casefold() in [x.casefold() for x in getattr(game, 'mechanics' if pref['kind'] == 'mechanic' else 'themes')]:
                strength = 1
            if strength:
                taste_signals.append(pref['sentiment'] * strength * pref['confidence'])
                matched.append(pref['subject'])
        taste = max(0, min(1, .5 + sum(taste_signals) / (2 * max(1, len(taste_signals)))))
        variant_scores = []
        for variant in variants:
            minutes = variant.session_minutes
            time_fit = .5 if minutes is None else min(1, context['minutes'] / max(1, minutes))
            if minutes and minutes > context['minutes'] and variant.pause_flexible:
                time_fit = max(time_fit, .7)
            attention_fit = .5 if variant.attention is None else 1 - max(0, variant.attention - context['energy']) / 2
            intensity_fit = .5 if variant.intensity is None else 1 - max(0, variant.intensity - context['energy']) / 2
            variant_scores.append(((time_fit + attention_fit + intensity_fit) / 3, variant))
        session_score, variant = max(variant_scores, key=lambda item: item[0])
        engagement = min(1, log1p(act.total_minutes or 0) / log1p(10000)) if act else 0
        components = {'taste': taste, 'session': session_score, 'engagement': engagement,
                      'feedback': 1 if game.pk in liked else .5}
        weighted = {key: round(value * inputs['weights'][key], 4) for key, value in components.items()}
        facts = {'platform': f'Listed for {context["platform"].title()}.',
                 'ownership': 'In your library.' if owned else 'Not known to be in your library.'}
        if context['mode'] != 'any':
            facts['mode'] = f'Supports {context["mode"]} play.'
        if matched:
            facts['taste'] = 'Matches preferences relating to ' + ', '.join(matched[:3]) + '.'
        if variant.session_minutes:
            facts['duration'] = f'Estimated session: {variant.session_minutes} minutes.'
        if variant.attention:
            facts['attention'] = f'Estimated attention required: {variant.get_attention_display().lower()}.'
        if variant.pause_flexible is True:
            facts['pause'] = 'Pause/save flexibility supports breaks.'
        if act and act.total_minutes:
            facts['engagement'] = f'{act.total_minutes} recorded minutes: engagement, not proof of enjoyment.'
        drawback = 'Suitability estimates are uncertain; your experience may differ.'
        if variant.session_minutes and variant.session_minutes > context['minutes']:
            drawback = 'The estimated session is longer than your available time.'
        elif variant.attention and variant.attention > context['energy']:
            drawback = 'May need more attention than you have available.'
        elif variant.pause_flexible is False:
            drawback = 'Stopping mid-session may be difficult.'
        elif variant.learning_effort == 3:
            drawback = 'Expect a substantial learning effort.'
        style = 'Something different'
        if variant.session_minutes and variant.session_minutes <= min(30, context['minutes']):
            style = 'Quick fun'
        elif variant.session_minutes and 60 <= variant.session_minutes <= context['minutes']:
            style = 'Longer immersion'
        elif variant.attention == 1:
            style = 'Low energy'
        if context['mode'] in ('coop', 'competitive'):
            style = 'Together' if context['mode'] == 'coop' else 'Competition'
        evidence = [{'attribute': e.attribute, 'value': e.value, 'confidence': e.confidence, 'verified': e.verified,
                     'source': e.source, 'url': e.source_url} for e in game.evidence.all()
                    if e.mode_id is None or (variant is not game and e.mode_id == variant.pk)]
        scores.append({'game_id': game.pk, 'title': game.title,
                       'mode': variant.name if variant is not game else '',
                       'score': round(sum(weighted.values()), 4), 'components': components,
                       'weighted': weighted, 'style': style, 'facts': facts,
                       'rationale': ' '.join(list(facts.values())[:4]), 'drawback': drawback,
                       'demonstration': game.demonstration, 'source_url': game.source_url,
                       'evidence': evidence, 'owned': owned,
                       'attributes': {'platforms': game.platforms, 'mechanics': game.mechanics,
                                      'themes': game.themes, **{key: getattr(variant, key) for key in
                                      ('session_minutes', 'attention', 'intensity', 'pause_flexible', 'learning_effort')}},
                       'activity': {'total_minutes': act.total_minutes, 'recent_minutes': act.recent_minutes,
                                    'observed_at': act.observed_at.isoformat() if act.observed_at else None} if act else None})
    scores.sort(key=lambda row: (-row['score'], row['game_id']))
    return scores, excluded


def select_variety(rows, limit=6):
    selected, styles, ids = [], set(), set()
    for row in rows:
        if row['style'] not in styles and row['game_id'] not in ids:
            selected.append(row)
            styles.add(row['style'])
            ids.add(row['game_id'])
        if len(selected) == limit:
            return selected
    for row in rows:
        if row['game_id'] not in ids:
            selected.append(row)
            ids.add(row['game_id'])
        if len(selected) == limit:
            break
    return selected


def create_run(user, context):
    inputs = snapshot_inputs(user, context)
    scores, excluded = rank(user, inputs)
    results = select_variety(scores)
    return RecommendationRun.objects.create(
        user=user, inputs=inputs, scores=scores, results=results, deterministic_results=results,
        scoring_version=SCORING_VERSION, status='pending' if settings.AI_ENABLED else 'deterministic',
        diagnostics={'excluded': excluded})


@transaction.atomic
def record_feedback(user, run, game_id, kind):
    if run.user_id != user.pk or game_id not in {r['game_id'] for r in run.results}:
        raise ValueError('Recommendation does not belong to this run.')
    if kind not in dict(Feedback._meta.get_field('kind').choices):
        raise ValueError('Unknown feedback.')
    game = Game.objects.get(pk=game_id)
    Feedback.objects.update_or_create(user=user, run=run, game=game, defaults={'kind': kind})
    if kind == 'dislike':
        from .games import save_preference
        save_preference(user, {'kind': 'game', 'subject': game.title, 'sentiment': -1,
                               'reason': 'Disliked from recommendations.'}, source='feedback')
