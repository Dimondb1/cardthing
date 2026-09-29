@echo off
REM Windows: double-click to fetch the latest prices from every shop set up in admin.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat ( echo Run start.bat first. & pause & exit /b 1 )
call .venv\Scripts\activate.bat
python manage.py import_prices
python manage.py snapshot_daily_prices
echo.
echo Done. Anything the shop sells that could not be matched is under Admin ^> Shop products to review.
pause
