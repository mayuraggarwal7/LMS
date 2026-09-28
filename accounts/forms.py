from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["role", "first_name", "last_name", "username", "email", "department", "year_of_study", "roll_no"]
        widgets = {"role": forms.RadioSelect}
        help_texts = {"username": ""}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["year_of_study"].help_text = "Students only"
        self.fields["roll_no"].help_text = "Students only"


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "department", "year_of_study", "roll_no"]
