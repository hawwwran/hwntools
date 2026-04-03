@echo off
setlocal enabledelayedexpansion
title HWN Tools - WSLg Setup

echo ============================================
echo   HWN Tools - WSLg Setup for Windows
echo ============================================
echo.

:: Check Windows version
for /f "tokens=4-5 delims=. " %%i in ('ver') do set VERSION=%%i.%%j
echo [*] Windows version: %VERSION%

:: Check if WSL is available
where wsl >nul 2>&1
if errorlevel 1 (
    echo.
    echo [!] WSL is not installed.
    echo     Run this in an Administrator PowerShell:
    echo.
    echo       wsl --install
    echo.
    echo     Then reboot and run this script again.
    goto :done
)
echo [OK] WSL is installed

:: Check WSL version
echo.
echo [*] Checking WSL status...
wsl --status >nul 2>&1
if errorlevel 1 (
    echo [!] WSL is installed but not configured.
    echo     Run: wsl --install
    goto :done
)

:: Check if any distro is installed
:: Use PowerShell to convert wsl.exe UTF-16 output to readable ASCII
powershell -NoProfile -Command "[Console]::OutputEncoding = [System.Text.Encoding]::Unicode; (wsl -l -v) | Select-Object -Skip 1 | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() -replace '  +', ' ' } | Set-Content -Path ($env:TEMP + '\wsl_distros.txt') -Encoding ASCII" 2>nul
for /f "usebackq tokens=1,2,3,4" %%a in ("%TEMP%\wsl_distros.txt") do (
    if not "%%a"=="" (
        set DISTRO_FOUND=1
        if "%%a"=="*" (
            echo [OK] Found distro: %%b ^(WSL %%d, default^)
            if "%%d"=="1" (
                echo [!] Distro "%%b" is running WSL1. Convert to WSL2:
                echo     wsl --set-version %%b 2
                set NEEDS_WSL2=1
            )
        ) else (
            echo [OK] Found distro: %%a ^(WSL %%c^)
            if "%%c"=="1" (
                echo [!] Distro "%%a" is running WSL1. Convert to WSL2:
                echo     wsl --set-version %%a 2
                set NEEDS_WSL2=1
            )
        )
    )
)
del "%TEMP%\wsl_distros.txt" >nul 2>&1

if not defined DISTRO_FOUND (
    echo [!] No WSL distro found.
    echo     Run: wsl --install
    echo     This will install Ubuntu by default.
    goto :done
)

if defined NEEDS_WSL2 (
    echo.
    echo [!] Some distros need WSL2 for WSLg to work.
    echo     Convert them first, then run this script again.
    goto :done
)

:: Check WSLg by testing DISPLAY inside WSL
echo.
echo [*] Checking WSLg support...
set "WSLG_DISPLAY="
for /f "delims=" %%d in ('wsl -- printenv DISPLAY 2^>nul') do set "WSLG_DISPLAY=%%d"

if defined WSLG_DISPLAY (
    echo [OK] WSLg is working ^(DISPLAY=%WSLG_DISPLAY%^)
) else (
    echo [!] WSLg does not seem active.
    echo     Make sure you have:
    echo       - Windows 11 ^(or Windows 10 21H2+^)
    echo       - WSL updated: wsl --update
    echo       - Reboot after updating
    echo.
    echo     Trying to update WSL now...
    wsl --update
    echo.
    echo     Please reboot and run this script again.
    goto :done
)


:: Check and install dependencies inside WSL
echo.
echo [*] Checking WSL dependencies...
set "MISSING_DEPS="
for %%p in (python3 gir1.2-gtk-3.0 gir1.2-vte-2.91) do (
    wsl -- dpkg -s %%p >nul 2>&1
    if errorlevel 1 set "MISSING_DEPS=!MISSING_DEPS! %%p"
)
if defined MISSING_DEPS (
    echo [!] Missing:%MISSING_DEPS%
    echo     Installing...
    echo     ^(you may be asked for your WSL password^)
    echo.
    wsl -- bash -c "sudo apt update && sudo apt install -y%MISSING_DEPS% && echo && echo '[OK] All dependencies installed'"
) else (
    echo [OK] All dependencies already installed
)

:: Find the script path inside WSL
echo.
echo [*] Detecting script path in WSL...
:: Get the Windows path of this script's directory
set "WINPATH=%~dp0"
if "%WINPATH:~-1%"=="\" set "WINPATH=%WINPATH:~0,-1%"
:: Convert to WSL path
for /f "delims=" %%p in ('wsl -- wslpath -a "%WINPATH%"') do set WSLPATH=%%p
echo     WSL path: %WSLPATH%

:: Create VBS launcher with baked-in WSL path
echo.
echo [*] Creating launcher...
set "VBSPATH=%~dp0hwntools.vbs"
echo Set s = CreateObject("WScript.Shell") > "%VBSPATH%"
echo ret = s.Run("wsl --exec pgrep -qf ""wslg-anchor""", 0, True) >> "%VBSPATH%"
echo If ret Then >> "%VBSPATH%"
echo     s.Run "wsl -- bash -c 'cd %WSLPATH% ^&^& python3 -m hwnlib.wslg_anchor'", 0, False >> "%VBSPATH%"
echo     WScript.Sleep 1000 >> "%VBSPATH%"
echo End If >> "%VBSPATH%"
echo s.Run "wsl -- python3 %WSLPATH%/hwntools.py", 0, False >> "%VBSPATH%"
if exist "%VBSPATH%" (
    echo [OK] Created hwntools.vbs
) else (
    echo [!] Failed to create hwntools.vbs
)

:: Create Start Menu shortcut with Ctrl+Shift+~ hotkey
echo.
echo [*] Creating keyboard shortcut Ctrl+Shift+~ ...
set "LNKPATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\HWN Tools.lnk"
powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%LNKPATH%'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%VBSPATH%\"'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7; $s.Save(); $b = [System.IO.File]::ReadAllBytes('%LNKPATH%'); $b[0x40] = 0xC0; $b[0x41] = 0x03; [System.IO.File]::WriteAllBytes('%LNKPATH%', $b)"
if exist "%LNKPATH%" (
    echo [OK] Ctrl+Shift+~ shortcut installed
) else (
    echo [!] Failed to create shortcut
)

echo.
echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo   Press Ctrl+Shift+~ or double-click hwntools.cmd to launch.
echo.
echo   Windows Explorer must be restarted for the keyboard
echo   shortcut to take effect.
echo.
set /p RESTART_EXPLORER="Restart Explorer now? (y/n): "
if /i "%RESTART_EXPLORER%"=="y" (
    echo Restarting Explorer...
    taskkill /f /im explorer.exe >nul 2>&1
    start explorer.exe
    echo [OK] Explorer restarted
)

:done
echo.
pause
