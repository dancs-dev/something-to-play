from django import forms
from .models import Preference


class PreferenceForm(forms.ModelForm):
    class Meta:
        model = Preference
        fields = ('subject', 'sentiment', 'reason')
        labels = {'subject': 'Game', 'sentiment': 'How did you feel about it?', 'reason': 'Why?'}
        widgets = {'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'The details help: exploration, story, combat, pacing…'})}


class RecommendationForm(forms.Form):
    context = forms.CharField(required=False, max_length=1000, label='Anything you want this time? (optional)',
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'For example: something relaxing I can play for half an hour.'}))
