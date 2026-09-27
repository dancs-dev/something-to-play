from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, FormView, TemplateView, UpdateView

from .forms import PreferenceForm, RecommendationForm
from .models import Preference, RecommendationRun
from .recommendations import RecommendationError, create_run


class HomeView(TemplateView):
    template_name = 'app/home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            context.update(
                preferences=Preference.objects.filter(user=self.request.user),
                preference_form=PreferenceForm(),
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
        return super().get_initial() | getattr(self, 'prefill', {})

    def get_context_data(self, **kwargs):
        return super().get_context_data(preference=self.object, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        with transaction.atomic():
            existing = self.get_queryset().filter(subject__iexact=form.cleaned_data['subject']).first()
            if existing and existing != self.object:
                for key, value in form.cleaned_data.items():
                    setattr(existing, key, value)
                existing.save()
                if self.object:
                    self.object.delete()
                self.object = existing
            else:
                self.object = form.save()
        return HttpResponseRedirect(self.get_success_url())


class PreferenceCreateView(PreferenceFormMixin, CreateView):
    pass


class PreferenceUpdateView(PreferenceFormMixin, UpdateView):
    pass


class PreferenceDeleteView(LoginRequiredMixin, DeleteView):
    model = Preference
    http_method_names = ['post']
    success_url = reverse_lazy('home')

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
            'preferences': Preference.objects.filter(user=self.request.user),
            'preference_form': PreferenceForm(),
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
