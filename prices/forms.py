from __future__ import annotations

from django import forms
from django.utils import timezone

from .models import PriceObservation, PriceSeries, Source


class PriceEvidenceForm(forms.Form):
    UNIT_CHOICES = (
        ("kg", "Per kilogram"),
        ("bag", "Bag (weight required)"),
        ("sack", "Sack (weight required)"),
        ("cavan", "Cavan/kaban (weight required)"),
    )

    series = forms.ModelChoiceField(queryset=PriceSeries.objects.none())
    source = forms.ModelChoiceField(queryset=Source.objects.none())
    observed_on = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    original_price = forms.DecimalField(min_value=0.0001, max_digits=12, decimal_places=4)
    original_unit = forms.ChoiceField(choices=UNIT_CHOICES)
    container_weight_kg = forms.DecimalField(
        required=False, min_value=0.001, max_digits=10, decimal_places=3
    )
    source_reference = forms.CharField(max_length=500)
    collection_method = forms.CharField(max_length=120, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = PriceSeries.objects.filter(is_active=True).order_by("name")
        self.fields["source"].queryset = Source.objects.filter(is_active=True).order_by("name")

    def clean_observed_on(self):
        observed_on = self.cleaned_data["observed_on"]
        if observed_on > timezone.localdate():
            raise forms.ValidationError("Observation date cannot be in the future.")
        return observed_on

    def clean(self):
        cleaned = super().clean()
        series = cleaned.get("series")
        source = cleaned.get("source")
        unit = cleaned.get("original_unit")
        weight = cleaned.get("container_weight_kg")
        if unit and unit != "kg" and not weight:
            self.add_error("container_weight_kg", "Enter the explicit kilogram weight.")
        if series and source:
            if series.scope == PriceSeries.Scope.DEMO and source.scope != Source.Scope.DEMO:
                self.add_error("source", "A demo series requires a demo source.")
            elif series.scope == PriceSeries.Scope.LOCAL and source.scope != Source.Scope.LOCAL:
                self.add_error("source", "A local series requires a local source.")
            elif series.scope == PriceSeries.Scope.REFERENCE and source.scope not in {
                Source.Scope.PROVINCIAL,
                Source.Scope.NATIONAL,
            }:
                self.add_error("source", "A reference series requires an official source.")
        return cleaned


class RevisionForm(PriceEvidenceForm):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, original: PriceObservation, **kwargs):
        self.original = original
        kwargs.setdefault(
            "initial",
            {
                "series": original.series,
                "source": original.source,
                "observed_on": original.observed_on,
                "original_price": original.original_price,
                "original_unit": original.original_unit,
                "container_weight_kg": original.container_weight_kg,
                "source_reference": original.source_reference,
                "collection_method": original.collection_method,
                "notes": original.notes,
            },
        )
        super().__init__(*args, **kwargs)
        self.fields["series"].disabled = True
        self.fields["source"].disabled = True


class CSVImportForm(forms.Form):
    series = forms.ModelChoiceField(queryset=PriceSeries.objects.none())
    source = forms.ModelChoiceField(queryset=Source.objects.none())
    csv_file = forms.FileField(
        help_text="UTF-8 CSV, maximum 5 MB and 5,000 rows. Data remain staged until verified."
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = PriceSeries.objects.filter(is_active=True).order_by("name")
        self.fields["source"].queryset = Source.objects.filter(is_active=True).order_by("name")

    def clean_csv_file(self):
        upload = self.cleaned_data["csv_file"]
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError("CSV upload exceeds the 5 MB limit.")
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("Upload a .csv file.")
        return upload

    def clean(self):
        cleaned = super().clean()
        series = cleaned.get("series")
        source = cleaned.get("source")
        if series and source:
            if series.scope == PriceSeries.Scope.DEMO and source.scope != Source.Scope.DEMO:
                self.add_error("source", "A demo series requires a demo source.")
            elif series.scope == PriceSeries.Scope.LOCAL and source.scope != Source.Scope.LOCAL:
                self.add_error("source", "A local series requires a local source.")
            elif series.scope == PriceSeries.Scope.REFERENCE and source.scope not in {
                Source.Scope.PROVINCIAL,
                Source.Scope.NATIONAL,
            }:
                self.add_error("source", "A reference series requires an official source.")
        return cleaned


class VerificationForm(forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    accept_outlier = forms.BooleanField(
        required=False,
        help_text="Confirm that the flagged value was checked against its original evidence.",
    )
    accept_conflict = forms.BooleanField(
        required=False,
        help_text="Confirm that the conflicting value and source evidence were reconciled.",
    )


class RejectionForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class PrimarySeriesForm(forms.Form):
    series = forms.ModelChoiceField(
        queryset=PriceSeries.objects.none(),
        help_text=(
            "This exact classification, condition, and location becomes public for its scope."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = PriceSeries.objects.filter(
            is_active=True, is_public=True
        ).order_by("scope", "name")
