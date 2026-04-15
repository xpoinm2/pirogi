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
echo     run_gui.bat (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/run_gui.bat.rej b/run_gui.bat.rej
deleted file mode 100644
index 1eaca5dbccfc868946f0d952f0d337c7d9001aa6..0000000000000000000000000000000000000000
--- a/run_gui.bat.rej
+++ /dev/null
@@ -1,19 +0,0 @@
-diff a/run_gui.bat b/run_gui.bat	(rejected hunks)
-@@ -1,13 +1,15 @@
- @echo off
- setlocal
- 
--if not exist .venv\Scripts\python.exe (
-+cd /d "%~dp0"
-+
-+if not exist ".venv\Scripts\python.exe" (
-     echo Virtual environment not found. Run setup_windows.bat first.
-     pause
-     exit /b 1
- )
- 
--call .venv\Scriptsctivate.bat
-+call ".venv\Scripts\activate.bat"
- python run.py gui
- 
- endlocal
 
EOF
)

echo Optional CLI commands:
echo     run_menu.bat

echo     .venv\Scripts\python.exe run.py dialogs

echo.
pause
