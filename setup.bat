@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo Installing Playwright Chromium browser...
playwright install chromium
echo.
echo Setup complete!
echo.
echo Next steps:
echo  1. Edit config.json with your Bot Token and Chat ID(s)
echo  2. Run:  python bot.py
pause
