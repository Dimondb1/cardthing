@echo off
REM Windows: double-click, paste a shop address, and see whether CardScout can import its prices.
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat ( echo Run start.bat first. & pause & exit /b 1 )
call .venv\Scripts\activate.bat
set /p url=Shop address (for example https://www.shopname.co.uk/): 
python manage.py check_shop "%url%"
echo.
pause
