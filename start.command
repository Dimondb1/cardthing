#!/bin/bash
# Mac: double-click this file to start CardScout on your computer.
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python is not installed. Get it from https://www.python.org/downloads/ then run this again."
  read -p "Press Enter to close."; exit 1
fi
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
python manage.py migrate -v0
python manage.py remove_demo
python manage.py setup_shops
if ! python manage.py shell -c "from django.contrib.auth import get_user_model as g; import sys; sys.exit(0 if g().objects.filter(is_superuser=True).exists() else 1)" 2>/dev/null; then
  echo
  echo "Create your admin login (used at http://127.0.0.1:8000/admin/)."
  python manage.py createsuperuser
fi
echo
echo "CardScout is running. Open http://127.0.0.1:8000/ in your browser."
echo "Admin is at http://127.0.0.1:8000/admin/. Close this window to stop."
(sleep 2; open http://127.0.0.1:8000/) &
python manage.py runserver 0.0.0.0:8000
