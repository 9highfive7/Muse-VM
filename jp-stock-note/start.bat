@echo off
rem One-command start for Windows (mirrors start.sh): sets up .venv if needed,
rem installs backend deps, builds the frontend if needed, migrates the DB and
rem serves everything on http://127.0.0.1:%PORT% (loopback only, default 8000).
setlocal EnableExtensions
cd /d "%~dp0"

if "%PORT%"=="" set "PORT=8000"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [start] creating Python venv ^(.venv^)
  set "PYLAUNCH="
  where py >nul 2>nul && set "PYLAUNCH=py -3"
  if not defined PYLAUNCH (
    where python >nul 2>nul && set "PYLAUNCH=python"
  )
  call :need_python
  if errorlevel 1 exit /b 1
  call %%PYLAUNCH%% -m venv .venv
  if errorlevel 1 (
    echo [start] ERROR: could not create .venv
    exit /b 1
  )
  "%VENV_PY%" -m pip install -q --upgrade pip
  "%VENV_PY%" -m pip install -q -r backend\requirements.txt
  if errorlevel 1 (
    echo [start] ERROR: pip install failed
    exit /b 1
  )
)

rem Build the frontend when dist is missing or any source is newer than dist\index.html.
set "NEED_BUILD="
if not exist "frontend\dist\index.html" set "NEED_BUILD=1"
if not defined NEED_BUILD (
  for /f %%R in ('powershell -NoProfile -Command "$d=(Get-Item 'frontend/dist/index.html').LastWriteTime; $n=@(Get-ChildItem -Recurse -File 'frontend/src','frontend/index.html' | Where-Object { $_.LastWriteTime -gt $d }).Count; Write-Output $n"') do (
    if not "%%R"=="0" set "NEED_BUILD=1"
  )
)
if defined NEED_BUILD (
  echo [start] building frontend
  where npm >nul 2>nul
  if errorlevel 1 (
    echo [start] ERROR: npm was not found. Install Node.js 20+ ^(https://nodejs.org/^) and run again.
    exit /b 1
  )
  pushd frontend
  if not exist "node_modules" (
    call npm install --no-audit --no-fund
    if errorlevel 1 ( popd & echo [start] ERROR: npm install failed & exit /b 1 )
  )
  call npm run build
  if errorlevel 1 ( popd & echo [start] ERROR: frontend build failed & exit /b 1 )
  popd
)

if not exist ".env" (
  echo [start] note: .env not found - the app runs, but fetches will be recorded as "API key not set".
  echo         copy .env.example .env  and set JQUANTS_API_KEY to enable J-Quants fetches.
)

pushd backend
"..\%VENV_PY%" -m stockapp.db
if errorlevel 1 ( popd & echo [start] ERROR: DB migration failed & exit /b 1 )
echo [start] serving on http://127.0.0.1:%PORT%  (Ctrl+C to stop)
"..\%VENV_PY%" -m stockapp.server --port %PORT%
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:need_python
if defined PYLAUNCH exit /b 0
echo [start] ERROR: Python 3.12 was not found (neither "py" nor "python").
echo         Install it from https://www.python.org/ and run start.bat again.
exit /b 1
