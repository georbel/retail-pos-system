from django import template

register = template.Library()


@register.filter
def get_field(record, name):
    value = getattr(record, name, "")
    return value() if callable(value) else value
