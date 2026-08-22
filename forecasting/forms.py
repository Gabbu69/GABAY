from __future__ import annotations

from django import forms

from prices.models import PriceSeries


class ModelExperimentForm(forms.Form):
    series = forms.ModelChoiceField(
        queryset=PriceSeries.objects.none(),
        empty_label="Choose a compatible price series",
    )
    through = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Optional historical cutoff. Leave blank for the latest verified week.",
    )
    publish_live = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Publish as live only when its target week is still in the future.",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = PriceSeries.objects.filter(is_active=True).order_by(
            "scope", "name"
        )


class FreezeRuleSetForm(forms.Form):
    rule_set_id = forms.IntegerField(widget=forms.HiddenInput)
    confirmation = forms.CharField(
        help_text="Type the exact rule-set version to confirm adviser approval.",
        max_length=30,
    )
