@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment with Python 3.11.
        echo Install Python 3.11+ and re-run setup_windows.bat.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

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