from typing import cast

from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.forms import Form
from django.http import HttpRequest, HttpResponse, HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import FormView


class SignupView(FormView):
    form_class = UserCreationForm
    template_name = "registration/signup.html"
    success_url = reverse_lazy("onboarding")

    def get_form(self, form_class: type[Form] | None = None) -> Form:
        form = super().get_form(form_class)
        for field in form.fields.values():
            field.help_text = ""
        return form

    def dispatch(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponseBase:
        if request.user.is_authenticated:
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form: UserCreationForm) -> HttpResponse:
        login(self.request, form.save())
        return cast(HttpResponse, super().form_valid(form))
