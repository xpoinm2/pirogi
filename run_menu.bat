@echo off
setlocal

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found. Run setup_windows.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python run.py menu

endlocal
