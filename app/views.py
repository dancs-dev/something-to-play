from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import PreferenceForm, RecommendationForm
from .models import Preference, RecommendationRun
from .recommendations import RecommendationError, create_run


def home_context(user):
    return {'preferences': Preference.objects.filter(user=user), 'preference_form': PreferenceForm(),
            'recommendation_form': RecommendationForm(),
            'run': RecommendationRun.objects.filter(user=user).order_by('-created_at').first()}


@require_GET
def home(request):
    context = home_context(request.user) if request.user.is_authenticated else {}
    return render(request, 'app/home.html', context)


@login_required
@require_GET
def profile(request):
    return redirect('home')


@login_required
@require_http_methods(['GET', 'POST'])
def preference(request, pk=None):
    instance = get_object_or_404(Preference, pk=pk, user=request.user) if pk else None
    initial = {}
    if request.method == 'GET' and request.GET.get('run'):
        try:
            run_id = int(request.GET['run'])
        except ValueError:
            return redirect('home')
        run = get_object_or_404(RecommendationRun, pk=run_id, user=request.user)
        try:
            index = int(request.GET.get('index', '-1'))
            if index < 0:
                raise IndexError
            initial = {'subject': run.results[index]['title'],
                       'sentiment': -1 if request.GET.get('feeling') == 'dislike' else 1}
        except (ValueError, IndexError, KeyError):
            return redirect('home')
    form = PreferenceForm(request.POST if request.method == 'POST' else None, instance=instance, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            existing = Preference.objects.filter(user=request.user, subject__iexact=form.cleaned_data['subject']).first()
            saved = existing or instance or Preference(user=request.user)
            for key, value in form.cleaned_data.items():
                setattr(saved, key, value)
            saved.save()
            if instance and instance.pk != saved.pk:
                instance.delete()
        return redirect('home')
    return render(request, 'app/preference.html', {'form': form, 'preference': instance})


@login_required
@require_POST
def delete_preference(request, pk):
    get_object_or_404(Preference, pk=pk, user=request.user).delete()
    return redirect('home')


@login_required
@require_POST
def recommend(request):
    form = RecommendationForm(request.POST)
    result = {'run': None}
    if form.is_valid():
        try:
            result['run'] = create_run(request.user, form.cleaned_data['context'])
        except RecommendationError as exc:
            result['error'] = str(exc)
    else:
        result['error'] = 'Please keep your current request under 1,000 characters.'
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'app/results.html', result)
    if result['run']:
        return redirect('run', pk=result['run'].pk)
    return render(request, 'app/home.html', home_context(request.user) | result | {'recommendation_form': form})


@login_required
@require_GET
def run_detail(request, pk):
    run = get_object_or_404(RecommendationRun, pk=pk, user=request.user)
    return render(request, 'app/run.html', {'run': run})


@login_required
@require_GET
def history(request):
    return render(request, 'app/history.html', {'runs': RecommendationRun.objects.filter(user=request.user).order_by('-created_at')[:50]})
