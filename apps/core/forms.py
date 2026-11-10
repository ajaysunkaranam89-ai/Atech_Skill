import re

from django import forms

from apps.accounts.forms import BootstrapMixin
from apps.courses.models import Course, CourseStatus

from .models import Enquiry


class EnquiryForm(BootstrapMixin, forms.ModelForm):
    # Honeypot: hidden from humans with CSS; bots that fill every field are rejected.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"autocomplete": "off", "tabindex": "-1"}))

    class Meta:
        model = Enquiry
        fields = ["name", "phone", "email", "course", "kind", "preferred_time", "message",
                  "company_name", "designation", "team_size", "training_mode"]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 2, "placeholder": "Anything you'd like us to know (optional)"}),
            "preferred_time": forms.TextInput(attrs={"placeholder": "e.g. Weekdays after 6 PM"}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "10-digit mobile number"}),
            "name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "Optional"}),
        }
        labels = {"name": "Your name", "phone": "Mobile / WhatsApp", "course": "Course", "preferred_time": "Best time to call"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(status=CourseStatus.PUBLISHED).order_by("title")
        self.fields["course"].required = False
        self.fields["course"].empty_label = "Not sure yet – help me choose"
        self.fields["kind"].choices = [c for c in self.fields["kind"].choices if c[0] not in ("corporate", "brochure")] \
            if not self.data or self.data.get("kind") not in ("corporate", "brochure") else self.fields["kind"].choices
        self._bootstrap()

    def clean_phone(self):
        raw = self.cleaned_data["phone"].strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if not (10 <= len(digits) <= 13):
            raise forms.ValidationError("Enter a valid mobile number.")
        return ("+91 " + digits) if len(digits) == 10 else ("+" + digits)

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("Spam detected.")
        return ""


class NewsletterForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "Your email", "class": "form-control", "autocomplete": "email"}))
    website = forms.CharField(required=False)  # honeypot
