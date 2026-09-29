#!/bin/bash
# Mac: double-click, paste a shop address, and see whether RipRaptor can import its prices.
cd "$(dirname "$0")"
. .venv/bin/activate 2>/dev/null || { echo "Run start.command first."; read -p "Press Enter to close."; exit 1; }
read -p "Shop address (for example https://www.shopname.co.uk/): " url
python manage.py check_shop "$url"
echo
read -p "Press Enter to close."
