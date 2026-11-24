@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python trong PATH.
    echo Vui long cai dat Python 3.10+ tu https://www.python.org/downloads/ va chon "Add Python to PATH".
    pause
    exit /b 1
)

python -c "import telethon, qrcode" >nul 2>&1
if errorlevel 1 (
    echo Dang cai dat thu vien tu requirements.txt...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [LOI] Cai dat telethon that bai. Vui long kiem tra ket noi mang hoac pip.
        pause
        exit /b 1
    )
)

python main.py %*
if errorlevel 1 (
    echo.
    echo Chuong trinh ket thuc voi ma loi %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
