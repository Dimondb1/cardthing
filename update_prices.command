#!/bin/bash
# Mac: double-click to fetch the latest prices from every shop set up in admin.
cd "$(dirname "$0")"
. .venv/bin/activate 2>/dev/null || { echo "Run start.command first."; read -p "Press Enter to close."; exit 1; }
python manage.py import_prices
python manage.py tidy_all
python manage.py snapshot_daily_prices
echo
echo "Done. Anything the shop sells that could not be matched is under Admin > Shop products to review."
read -p "Press Enter to close."
