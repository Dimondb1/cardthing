@echo off
REM Windows: double-click this file to start CardScout on your computer.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Get it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
  pause
  exit /b 1
)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
python manage.py migrate -v0
python manage.py seed_catalogue
python manage.py shell -c "from django.contrib.auth import get_user_model as g; import sys; sys.exit(0 if g().objects.filter(is_superuser=True).exists() else 1)" >nul 2>nul
if errorlevel 1 (
  echo.
  echo Create your admin login ^(used at http://127.0.0.1:8000/admin/^).
  python manage.py createsuperuser
)
echo.
echo CardScout is running. Open http://127.0.0.1:8000/ in your browser.
echo Admin is at http://127.0.0.1:8000/admin/. Close this window to stop.
start "" http://127.0.0.1:8000/
python manage.py runserver 0.0.0.0:8000
