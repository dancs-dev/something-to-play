from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .forms import ConversationForm, OwnershipForm, PreferenceForm, SessionForm
from .games import save_preference
from .models import ConversationTurn, Game, OwnershipActivity, Preference, RecommendationRun, SteamAccount
from .recommendations import create_run, record_feedback


@require_GET
def home(request):
    return render(request, 'app/home.html', {'form': SessionForm()})


@login_required
@require_http_methods(['GET', 'POST'])
def onboarding(request):
    step = request.GET.get('step', 'favourites')
    if step not in ('favourites', 'dislikes', 'ownership'):
        step = 'favourites'
    form = PreferenceForm(request.POST or None, initial={'sentiment': -1 if step == 'dislikes' else 1})
    if request.method == 'POST' and form.is_valid():
        save_preference(request.user, form.cleaned_data)
        messages.success(request, 'Saved. Add another, or continue to the next question.')
        return redirect(request.path + '?step=' + step)
    return render(request, 'app/onboarding.html', {'form': form, 'step': step,
        'games': Game.objects.all(), 'ownership_form': OwnershipForm()})


@login_required
@require_GET
def profile(request):
    return render(request, 'app/profile.html', {
        'preferences': Preference.objects.filter(user=request.user).order_by('-updated_at'),
        'form': PreferenceForm(), 'games': Game.objects.all(), 'ownership_form': OwnershipForm(),
        'activities': OwnershipActivity.objects.filter(user=request.user).select_related('game'),
        'steam': SteamAccount.objects.filter(user=request.user).first(),
        'steam_api_available': bool(settings.STEAM_API_KEY),
        'conversation_form': ConversationForm(), 'ai_enabled': settings.AI_ENABLED,
        'turns': ConversationTurn.objects.filter(user=request.user).order_by('-created_at')[:10]})


@login_required
@require_http_methods(['GET', 'POST'])
def preference(request, pk=None):
    instance = get_object_or_404(Preference, pk=pk, user=request.user) if pk else None
    form = PreferenceForm(request.POST or None, instance=instance)
    if request.method == 'POST' and form.is_valid():
        # A renamed preference replaces the old record after validating the new one.
        from django.db import transaction
        with transaction.atomic():
            saved = save_preference(request.user, form.cleaned_data)
            if instance and instance.pk != saved.pk:
                Preference.objects.filter(pk=instance.pk, user=request.user).delete()
        return redirect('profile')
    return render(request, 'app/preference.html', {'form': form, 'preference': instance, 'games': Game.objects.all()})


@login_required
@require_POST
def delete_preference(request, pk):
    get_object_or_404(Preference, pk=pk, user=request.user).delete()
    return redirect('profile')


@login_required
@require_POST
def ownership(request):
    form = OwnershipForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Select a game and ownership status.')
    OwnershipActivity.objects.update_or_create(user=request.user, game=form.cleaned_data['game'],
        defaults={'manual_owned': {'yes': True, 'no': False, 'unknown': None}[form.cleaned_data['owned']]})
    messages.success(request, 'Ownership updated.')
    return redirect('profile')


@login_required
@require_POST
def recommend(request):
    form = SessionForm(request.POST)
    if not form.is_valid():
        return render(request, 'app/home.html', {'form': form}, status=400)
    run = create_run(request.user, form.cleaned_data)
    return redirect('run', pk=run.pk)


@login_required
@require_GET
def run_detail(request, pk):
    run = get_object_or_404(RecommendationRun, pk=pk, user=request.user)
    suppressed = set(run.feedback.filter(kind__in=['not_tonight', 'dislike']).values_list('game_id', flat=True))
    context = {'run': run, 'results': [r for r in run.results if r['game_id'] not in suppressed],
               'poll': run.status in ('pending', 'processing')}
    template = 'app/results.html' if request.headers.get('HX-Request') == 'true' else 'app/run.html'
    return render(request, template, context)


@login_required
@require_GET
def history(request):
    return render(request, 'app/history.html', {'runs': RecommendationRun.objects.filter(user=request.user).order_by('-created_at')[:50]})


@login_required
@require_POST
def feedback(request, pk):
    run = get_object_or_404(RecommendationRun, pk=pk, user=request.user)
    try:
        record_feedback(request.user, run, int(request.POST.get('game_id', '')), request.POST.get('kind'))
    except (ValueError, Game.DoesNotExist):
        return HttpResponseBadRequest('Invalid recommendation feedback.')
    messages.success(request, 'Feedback saved. “Not tonight” only affects this session context today.')
    return redirect('run', pk=run.pk)


@login_required
@require_POST
def conversation(request):
    form = ConversationForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Enter up to 4,000 characters.')
    turn = ConversationTurn.objects.create(user=request.user, text=form.cleaned_data['text'])
    if settings.AI_ENABLED:
        from .llm import OpenAIProvider, ProviderError, EXTRACTION_VERSION
        try:
            recent = list(ConversationTurn.objects.filter(user=request.user, pk__lt=turn.pk).order_by('-pk').values_list('text', flat=True)[:2])
            proposals = OpenAIProvider().extract_preferences(list(reversed(recent)) + [turn.text])
            turn.proposals = [p.model_dump() for p in proposals.preferences]
            turn.status = 'proposed'
            turn.prompt_version = EXTRACTION_VERSION
        except ProviderError:
            turn.status = 'failed'
        turn.save(update_fields=['proposals', 'status', 'prompt_version'])
    return redirect('profile')


@login_required
@require_POST
def confirm_proposal(request, pk, index):
    turn = get_object_or_404(ConversationTurn, pk=pk, user=request.user)
    if not 0 <= index < len(turn.proposals) or turn.proposals[index].get('confirmed'):
        return HttpResponseBadRequest('Invalid or already confirmed proposal.')
    proposal = turn.proposals[index]
    # The editable form is validated again; inference becomes explicit only here.
    form = PreferenceForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Invalid preference.')
    save_preference(request.user, form.cleaned_data, source='conversation_confirmed')
    proposal['confirmed'] = True
    turn.save(update_fields=['proposals'])
    return redirect('profile')


@login_required
@require_POST
def request_sync(request):
    steam = get_object_or_404(SteamAccount, user=request.user)
    steam.sync_requested = True
    steam.save(update_fields=['sync_requested'])
    messages.success(request, 'Sync requested. Your saved library remains available while you wait.')
    return redirect('profile')
