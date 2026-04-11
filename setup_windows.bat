@echo off
setlocal

if not exist .venv (
    py -3.11 -m venv .venv
)

call .venv\Scriptsctivate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo.
echo Setup finished.
echo Copy .env.example to config\.env and fill API_ID / API_HASH.
echo Then run one of:
echo     run_gui.bat

echo Optional CLI commands:
echo     run_menu.bat

echo     .venv\Scripts\python.exe run.py dialogs

echo.
pause
