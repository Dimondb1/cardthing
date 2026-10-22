"""Filters for the owner's dashboard (web/templates/web/dashboard.html)."""

from django import template

from catalogue import traffic

register = template.Library()


@register.filter
def number(value):
    """4,386 for whole numbers and 0.4 for a ratio, as the traffic explainer writes them; "" for nothing."""
    if value is None or value == "":
        return ""
    return traffic.number(value)


@register.filter
def clock(moment):
    """A time as 14:20 today, or 10 Oct 14:20 on another day, as Insights and Crawl health write them."""
    from django.utils import timezone

    from catalogue.insights import clock as insights_clock

    return insights_clock(moment, timezone.now()) if moment else ""


@register.filter
def join_dates(dates):
    """Days as "Tue 14 Oct, Wed 15 Oct"."""
    return ", ".join(f"{day:%a %-d %b}" for day in dates)


@register.filter
def percent(value):
    """A rate as 1.3% (one decimal place), or an en dash when there is nothing to divide by."""
    if value is None:
        return "–"
    return f"{value:.1f}%"
