@echo off
rem Starts WhyChain: the ERP database (Docker), the Meterless memory service, and the web app.
rem Double-click this file, or run  start.cmd  from the project folder.
cd /d "%~dp0"

echo Starting the ERP database...
docker compose up -d

echo Starting the Meterless memory service...
start "WhyChain - Meterless memory" cmd /k "cd meterless_bridge && npm start"

echo Starting the web app...
start "WhyChain - web app" cmd /k ".venv\Scripts\python -m whychain.api"

timeout /t 8 /nobreak >nul
start http://localhost:8000
echo WhyChain is running at http://localhost:8000  (close the two new windows to stop it)
