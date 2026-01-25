from django import forms

class TxtImportForm(forms.Form):
    soubor = forms.FileField(label="TXT jídelníček")
