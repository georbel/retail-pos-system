from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Branch, CompanyProfile, Department, PaymentMethod, Product, Supplier, UnitOfMeasure, User


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


class ProductForm(StyledModelForm):
    class Meta:
        model = Product
        exclude = ["created_at", "updated_at"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("department", "unit", "supplier"):
            self.fields[field_name].queryset = self.fields[field_name].queryset.filter(active=True)

    def clean_barcode(self):
        return self.cleaned_data.get("barcode") or None


class SupplierForm(StyledModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "contact_person", "phone", "email", "address", "active"]


class BranchForm(StyledModelForm):
    class Meta:
        model = Branch
        fields = ["name", "code", "address", "contact", "active"]


class DepartmentForm(StyledModelForm):
    class Meta:
        model = Department
        fields = ["name", "active"]


class UnitForm(StyledModelForm):
    class Meta:
        model = UnitOfMeasure
        fields = ["name", "abbreviation", "active"]


class PaymentMethodForm(StyledModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "kind", "active"]


class CompanyForm(StyledModelForm):
    class Meta:
        model = CompanyProfile
        fields = ["name", "address", "contact", "version"]


class UserForm(forms.ModelForm):
    password = forms.CharField(required=False, widget=forms.PasswordInput(attrs={"class": "input"}))

    class Meta:
        model = User
        fields = ["username", "full_name", "email", "role", "branch", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")
        self.fields["branch"].queryset = Branch.objects.filter(active=True)

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if not self.instance.pk and not password:
            raise forms.ValidationError("A password is required for a new user.")
        if password:
            try:
                validate_password(password, self.instance)
            except ValidationError as error:
                raise forms.ValidationError(error.messages) from error
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
            self.save_m2m()
        return user


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class StockAdjustmentForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(active=True))
    branch = forms.ModelChoiceField(queryset=Branch.objects.filter(active=True))
    quantity_change = forms.DecimalField(max_digits=12, decimal_places=3)
    note = forms.CharField(max_length=240)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "input"
        self.fields["branch"].queryset = Branch.objects.filter(active=True)
