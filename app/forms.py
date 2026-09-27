from django import forms
from .models import Preference


class PreferenceForm(forms.ModelForm):
    subject = forms.CharField(label='Game', max_length=200)
    game_id = forms.IntegerField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Preference
        fields = ('sentiment', 'reason')
        labels = {'sentiment': 'How did you feel about it?', 'reason': 'Why?'}
        widgets = {'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'The details help: exploration, story, combat, pacing…'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sentiment'].choices = [(2, 'Loved'), (1, 'Like'), (-1, 'Dislike'), (-2, 'Not played yet')]
        self.order_fields(['subject', 'game_id', 'sentiment', 'reason'])


class SteamLinkForm(forms.Form):
    profile = forms.CharField(max_length=200, label='Steam ID or profile URL')


class GameSearchForm(forms.Form):
    query = forms.CharField(max_length=200, label='Game title')


class RecommendationForm(forms.Form):
    context = forms.CharField(required=False, max_length=1000, label='Anything you want this time? (optional)',
        widget=forms.Textarea(attrs={'rows': 2, 'placeholder': 'For example: something relaxing I can play for half an hour.'}))
