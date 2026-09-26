from django import forms
from .models import Game, Preference, PLATFORMS, PLAY_MODES, SCOPES, LEVELS


class PreferenceForm(forms.ModelForm):
    class Meta:
        model = Preference
        fields = ('kind', 'subject', 'sentiment', 'reason')
        labels = {'subject': 'Game, mechanic or theme', 'reason': 'What worked for you, or got in the way?'}
        widgets = {'reason': forms.Textarea(attrs={'rows': 3}),
                   'subject': forms.TextInput(attrs={'list': 'game-titles'})}

    def clean_subject(self):
        return self.cleaned_data['subject'].strip().casefold()


class SessionForm(forms.Form):
    minutes = forms.IntegerField(min_value=5, max_value=720, initial=45, label='Minutes available')
    energy = forms.TypedChoiceField(choices=LEVELS, coerce=int, initial=2)
    platform = forms.ChoiceField(choices=PLATFORMS)
    mode = forms.ChoiceField(choices=PLAY_MODES, label='How are you playing?')
    scope = forms.ChoiceField(choices=SCOPES, initial='either', label='Choose from')


class OwnershipForm(forms.Form):
    game = forms.ModelChoiceField(queryset=Game.objects.all())
    owned = forms.TypedChoiceField(choices=[('yes', 'I own this'), ('no', 'I do not own this'), ('unknown', 'Use Steam / unknown')])


class ConversationForm(forms.Form):
    text = forms.CharField(max_length=4000, label='Tell me about games you enjoy, and why',
                          widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'I like exploring in… but dislike…'}))
