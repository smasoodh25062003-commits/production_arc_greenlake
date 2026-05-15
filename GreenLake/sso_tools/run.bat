@echo off
REM Standalone SSO Tools (Flask only). Prefer: python main.py from repo root for full Platform Tools.
cd /d "%~dp0\.."
echo ==========================================
echo   SSO Tools (HPE Role String + SAML)
echo   Default: http://127.0.0.1:5051/  (override with set SSO_TOOLS_PORT=...)
echo   Press Ctrl+C to stop.
echo ==========================================
echo.
python -m sso_tools
echo.
pause
