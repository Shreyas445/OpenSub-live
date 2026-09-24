@echo off
echo ========================================================
echo       OpenSub Live - Installing Dependencies
echo ========================================================
python -m pip install --upgrade pip
python -m pip install -r "%~dp0..\requirements.txt"
echo.
echo ========================================================
echo  All dependencies installed successfully!
echo  Run 'run_app.bat' to start OpenSub Live.
echo ========================================================
pause
