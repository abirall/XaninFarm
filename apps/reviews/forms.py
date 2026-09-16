"""Review forms."""

from __future__ import annotations

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.reviews.models import RATING_CHOICES, Review

MIN_BODY_LENGTH = 15


class ReviewForm(forms.ModelForm):
    """What a customer may set. Everything else - the verified-purchase flag,
    the order line, the moderation status - is decided server-side."""

    rating = forms.TypedChoiceField(
        label=_("Your rating"),
        choices=RATING_CHOICES,
        coerce=int,
        widget=forms.RadioSelect,
        error_messages={"required": _("Please choose a star rating.")},
    )

    class Meta:
        model = Review
        fields = ("rating", "title", "body")
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": _("Sum it up in a few words"), "maxlength": 120}
            ),
            "body": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": _("How was the quality, the freshness, the delivery?"),
                    "maxlength": 2000,
                }
            ),
        }
        labels = {"title": _("Headline (optional)"), "body": _("Your review")}

    def clean_body(self) -> str:
        body = (self.cleaned_data.get("body") or "").strip()
        if len(body) < MIN_BODY_LENGTH:
            raise forms.ValidationError(
                _("Please write at least %(count)d characters so others can learn from it.")
                % {"count": MIN_BODY_LENGTH}
            )
        return body


class ReviewReplyForm(forms.Form):
    """Staff reply, posted from the dashboard."""

    reply = forms.CharField(
        label=_("Reply from the farm"),
        max_length=1000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
