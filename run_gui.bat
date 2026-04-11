@echo off
setlocal

if not exist .venv\Scripts\python.exe (
    echo Virtual environment not found. Run setup_windows.bat first.
    pause
    exit /b 1
)

call .venv\Scriptsctivate.bat
python run.py gui

endlocal
