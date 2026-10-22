@echo off
rem Starts WhyChain from cold: Docker, the Zetaris platform, the ERP database, the Meterless memory service and the
rem web app, then warms them up. Double-click this file, or run  start.cmd  from the project folder.
rem Zetaris runs from its own Docker bundle: put its folder in .env as ZETARIS_DIR=C:\path\to\zetaris-platform
cd /d "%~dp0"

for /f "usebackq tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="ZETARIS_DIR" set "ZETARIS_DIR=%%b"

docker info >nul 2>&1
if not errorlevel 1 goto dockerready
echo Starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
:waitdocker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if errorlevel 1 goto waitdocker
:dockerready

if defined ZETARIS_DIR (
  echo Starting Zetaris from %ZETARIS_DIR% ...
  pushd "%ZETARIS_DIR%"
  docker compose up -d
  popd
) else (
  echo ZETARIS_DIR is not set in .env: Zetaris not started. WhyChain falls back to its local harness if Zetaris is down.
)

echo Starting the ERP database...
docker compose up -d

echo Starting the Meterless memory service...
start "WhyChain - Meterless memory" cmd /k "cd meterless_bridge && npm start"

echo Starting the web app...
start "WhyChain - web app" cmd /k ".venv\Scripts\python -m whychain.api"

echo Warming up Zetaris and the app (a cold Zetaris takes a few minutes)...
.venv\Scripts\python scripts\warmup.py

start http://localhost:8000
echo WhyChain is running at http://localhost:8000  (close the two new windows to stop it)
