from __future__ import annotations

from decimal import Decimal

from django import forms


class OfferCheckForm(forms.Form):
    offer_price = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_value=Decimal("9999.99"),
        max_digits=6,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "autocomplete": "off",
                "inputmode": "decimal",
                "min": "0.01",
                "max": "9999.99",
                "step": "0.01",
                "placeholder": "e.g. 22.50",
                "aria-describedby": "offer-help offer-privacy",
            }
        ),
    )

    def __init__(self, *args, language: str = "en", **kwargs):
        super().__init__(*args, **kwargs)
        if language == "tl":
            self.fields["offer_price"].label = "Presyo ng alok (PHP kada kilo)"
            self.fields["offer_price"].help_text = "Ilagay ang presyong alok sa iyo ngayon."
        else:
            self.fields["offer_price"].label = "Offer price (PHP per kilogram)"
            self.fields["offer_price"].help_text = "Enter the price being offered to you today."
