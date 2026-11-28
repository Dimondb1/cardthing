#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys


def lower_priority(environ=None, nice=None):
    """Give way to the website: a command started with RIPRAPTOR_NICE set (the server's scheduled jobs set 10,
    deploy/crontab) runs at that much lower priority, so a visitor's page is served first when both want a
    processor. The owner, 26 Nov 2026: "website responsiveness must take priority over background scraping
    activity". Anything but a whole number from 1 to 19 changes nothing, and nothing ever stops the command."""
    value = (os.environ if environ is None else environ).get("RIPRAPTOR_NICE", "")
    if not value.isdigit() or not 1 <= int(value) <= 19:
        return 0
    try:
        (nice or os.nice)(int(value))
    except (AttributeError, OSError):   # no os.nice on Windows; refused by the system
        return 0
    return int(value)


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ripraptor.settings")
    lower_priority()
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
