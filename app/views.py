from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, DetailView, FormView, TemplateView, UpdateView, View

from .forms import GameSearchForm, PreferenceForm, RecommendationForm, SteamLinkForm
from .library import refresh_catalogue, sync_account
from .models import CatalogueState, Game, LinkedAccount, Ownership, Preference, RecommendationRun
from .recommendations import RecommendationError, create_run
from .steam import SteamError, resolve_profile


def find_game(title, game_id=None):
    title = title.strip()
    if game_id:
        selected = get_object_or_404(Game, pk=game_id)
        if selected.title == title:
            return selected
    game = Game.objects.filter(title__iexact=title).first()
    if game:
        return game
    return Game.objects.create(title=title)


class HomeView(TemplateView):
    template_name = 'app/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context.update(
                preferences=Preference.objects.filter(user=self.request.user).exclude(sentiment=0).select_related('game')[:5],
                steam_connected=LinkedAccount.objects.filter(user=self.request.user, provider='steam').exists(),
                recommendation_form=RecommendationForm(),
                run=RecommendationRun.objects.filter(user=self.request.user).order_by('-created_at').first(),
            )
        return context


class ProfileView(LoginRequiredMixin, TemplateView):
    def get(self, request, *args, **kwargs):
        return redirect('home')


class PreferenceFormMixin(LoginRequiredMixin):
    http_method_names = ['get', 'post', 'head', 'options']
    model = Preference
    form_class = PreferenceForm
    template_name = 'app/preference.html'
    success_url = reverse_lazy('home')

    def get_success_url(self):
        return reverse_lazy('library') if self.request.GET.get('next') == 'library' else super().get_success_url()

    def get_queryset(self):
        return Preference.objects.filter(user=self.request.user)

    def get(self, request, *args, **kwargs):
        self.prefill = {}
        if request.GET.get('run'):
            try:
                run_id = int(request.GET['run'])
            except ValueError:
                return redirect('home')
            run = get_object_or_404(RecommendationRun, pk=run_id, user=request.user)
            try:
                index = int(request.GET.get('index', '-1'))
                if index < 0:
                    raise IndexError
                self.prefill = {
                    'subject': run.results[index]['title'],
                    'sentiment': -1 if request.GET.get('feeling') == 'dislike' else 1,
                }
            except (ValueError, IndexError, KeyError):
                return redirect('home')
        return super().get(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial() | getattr(self, 'prefill', {})
        if self.object:
            initial |= {'subject': self.object.game.title, 'game_id': self.object.game_id}
        elif self.request.GET.get('game'):
            game = get_object_or_404(Game, pk=self.request.GET['game'])
            initial |= {'subject': game.title, 'game_id': game.pk}
        elif self.request.GET.get('title'):
            initial['subject'] = self.request.GET['title'][:200]
        if self.request.GET.get('feeling') in {'like', 'dislike'}:
            initial['sentiment'] = 1 if self.request.GET['feeling'] == 'like' else -1
        return initial

    def get_context_data(self, **kwargs):
        return super().get_context_data(preference=self.object, **kwargs)

    def form_valid(self, form):
        try:
            game = find_game(form.cleaned_data['subject'], form.cleaned_data['game_id'])
        except SteamError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        with transaction.atomic():
            existing = self.get_queryset().filter(game=game).first()
            if existing and existing != self.object:
                existing.sentiment = form.cleaned_data['sentiment']
                existing.reason = form.cleaned_data['reason']
                existing.save(update_fields=['sentiment', 'reason', 'updated_at'])
                if self.object:
                    self.object.delete()
                self.object = existing
            else:
                self.object = form.save(commit=False)
                self.object.user = self.request.user
                self.object.game = game
                self.object.save()
        return HttpResponseRedirect(self.get_success_url())


class PreferenceCreateView(PreferenceFormMixin, CreateView):
    pass


class PreferenceUpdateView(PreferenceFormMixin, UpdateView):
    pass


class PreferenceDeleteView(LoginRequiredMixin, DeleteView):
    model = Preference
    http_method_names = ['post']
    success_url = reverse_lazy('home')

    def get_success_url(self):
        return reverse_lazy('library') if self.request.GET.get('next') == 'library' else super().get_success_url()

    def get_queryset(self):
        return Preference.objects.filter(user=self.request.user)


class RecommendationView(LoginRequiredMixin, FormView):
    http_method_names = ['post', 'options']
    form_class = RecommendationForm
    template_name = 'app/home.html'

    def render_result(self, result, form=None):
        if self.request.headers.get('HX-Request') == 'true':
            return render(self.request, 'app/results.html', result)
        context = {
            'preferences': Preference.objects.filter(user=self.request.user).exclude(sentiment=0).select_related('game')[:5],
            'steam_connected': LinkedAccount.objects.filter(user=self.request.user, provider='steam').exists(),
            'recommendation_form': form or RecommendationForm(),
            'run': RecommendationRun.objects.filter(user=self.request.user).order_by('-created_at').first(),
            **result,
        }
        return render(self.request, self.template_name, context)

    def form_valid(self, form):
        try:
            run = create_run(self.request.user, form.cleaned_data['context'])
        except RecommendationError as exc:
            return self.render_result({'run': None, 'error': str(exc)}, form)
        if self.request.headers.get('HX-Request') == 'true':
            return self.render_result({'run': run})
        return redirect('run', pk=run.pk)

    def form_invalid(self, form):
        return self.render_result(
            {'run': None, 'error': 'Please keep your current request under 1,000 characters.'}, form
        )


class RunDetailView(LoginRequiredMixin, DetailView):
    model = RecommendationRun
    context_object_name = 'run'
    template_name = 'app/run.html'

    def get_queryset(self):
        return RecommendationRun.objects.filter(user=self.request.user)


class HistoryView(LoginRequiredMixin, TemplateView):
    template_name = 'app/history.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            runs=RecommendationRun.objects.filter(user=self.request.user).order_by('-created_at')[:50], **kwargs
        )


class GameSearchView(LoginRequiredMixin, TemplateView):
    template_name = 'app/game_search.html'

    def post(self, request, *args, **kwargs):
        return self.render_to_response(self.get_context_data(**kwargs))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.POST.get('query', '').strip()[:200]
        context['form'] = GameSearchForm(initial={'query': query})
        context['query'] = query
        context['feeling'] = self.request.POST.get('feeling', 'like')
        context['results'] = []
        if query:
            exact = Game.objects.filter(title__iexact=query).first()
            context['exact_match'] = bool(exact)
            matches = Game.objects.filter(title__icontains=query)
            if exact:
                context['results'] = [exact, *matches.exclude(pk=exact.pk).order_by('title')[:19]]
            else:
                context['results'] = list(matches.order_by('title')[:20])
        return context


class CatalogueRefreshView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        try:
            refresh_catalogue()
            messages.success(request, 'Steam game catalogue refreshed.')
        except SteamError as exc:
            messages.error(request, str(exc))
        return redirect('settings')


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'app/settings.html'

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            account=LinkedAccount.objects.filter(user=self.request.user, provider='steam').first(),
            link_form=SteamLinkForm(),
            catalogue=CatalogueState.objects.filter(provider='steam').first(),
            **kwargs,
        )


class LibraryView(LoginRequiredMixin, TemplateView):
    template_name = 'app/library.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account = LinkedAccount.objects.filter(user=self.request.user, provider='steam').first()
        context['account'] = account
        context['recently_synced'] = bool(account and account.last_synced_at and account.last_synced_at > timezone.now() - timedelta(days=1))
        preferences = list(Preference.objects.filter(user=self.request.user).select_related('game'))
        context['rated_sections'] = [
            ('Liked', [pref for pref in preferences if pref.sentiment == 1]),
            ('Disliked', [pref for pref in preferences if pref.sentiment == -1]),
        ]
        context['ignored'] = [pref for pref in preferences if pref.sentiment == 0]
        if account:
            rated_ids = {pref.game_id for pref in preferences}
            context['unrated'] = [entry for entry in Ownership.objects.filter(account=account, is_active=True).select_related('game').order_by('game__title') if entry.game_id not in rated_ids]
        return context


class SteamLinkView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        form = SteamLinkForm(request.POST)
        if form.is_valid():
            try:
                steam_id = resolve_profile(form.cleaned_data['profile'])
                account, _ = LinkedAccount.objects.get_or_create(user=request.user, provider='steam', defaults={'external_user_id': steam_id})
                if account.external_user_id != steam_id:
                    account.external_user_id = steam_id
                    account.last_synced_at = None
                    account.save(update_fields=['external_user_id', 'last_synced_at'])
                    Ownership.objects.filter(account=account).update(is_active=False)
                messages.success(request, 'Steam profile linked. Sync your games from Library.')
            except SteamError as exc:
                messages.error(request, str(exc))
        else:
            messages.error(request, 'Enter a Steam ID or profile URL.')
        return redirect('settings')


class SteamSyncView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request):
        account = get_object_or_404(LinkedAccount, user=request.user, provider='steam')
        try:
            count = sync_account(account)
            messages.success(request, f'Synced {count} Steam games.')
        except SteamError as exc:
            messages.error(request, str(exc))
        return redirect('library')


class LibraryCurationView(LoginRequiredMixin, View):
    http_method_names = ['post']

    def post(self, request, game_id):
        get_object_or_404(Ownership, account__user=request.user, game_id=game_id)
        try:
            sentiment = int(request.POST.get('sentiment', ''))
        except ValueError:
            sentiment = None
        if sentiment not in {-1, 0, 1}:
            messages.error(request, 'Choose Like, Dislike, or Ignore.')
            return redirect('library')
        if sentiment != 0:
            preference = Preference.objects.filter(user=request.user, game_id=game_id).first()
            feeling = 'like' if sentiment == 1 else 'dislike'
            if preference:
                return redirect(f"{reverse_lazy('preference_edit', kwargs={'pk': preference.pk})}?feeling={feeling}&next=library")
            return redirect(f"{reverse_lazy('preference_new')}?game={game_id}&feeling={feeling}&next=library")
        with transaction.atomic():
            preference, _ = Preference.objects.get_or_create(user=request.user, game_id=game_id, defaults={'sentiment': sentiment})
            if preference.sentiment != sentiment:
                preference.sentiment = sentiment
                preference.save(update_fields=['sentiment', 'updated_at'])
        return redirect('library')
