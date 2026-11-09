from django import forms

from apps.accounts.forms import BootstrapMixin
from apps.courses.models import AssignmentSubmission


class SubmissionForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = AssignmentSubmission
        fields = ["text_answer", "file", "link"]
        widgets = {"text_answer": forms.Textarea(attrs={"rows": 6})}
        labels = {"text_answer": "Your answer", "file": "Attach file (PDF/ZIP/Office)", "link": "Link (e.g. GitHub repo)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bootstrap()

    def clean(self):
        cd = super().clean()
        if not (cd.get("text_answer") or cd.get("file") or cd.get("link")):
            raise forms.ValidationError("Provide an answer, a file or a link.")
        return cd
