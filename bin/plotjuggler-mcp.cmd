@echo off
rem SPDX-License-Identifier: MPL-2.0
rem Windows twin of bin/plotjuggler-mcp: starts `PlotJuggler4 --mcp-bridge` (MCP over stdio).
rem Order: %PLOTJUGGLER4%, PATH, the path the app records at every start, the installer's default folder.
rem Without a desktop install (or with PJ_BROWSER=1) it starts the browser relay instead.
setlocal
if "%PJ_BROWSER%"=="1" goto browser
set "EXE=%PLOTJUGGLER4%"
if defined EXE if exist "%EXE%" goto run
for %%E in (PlotJuggler4.exe) do set "EXE=%%~$PATH:E"
if defined EXE goto run
set "REC=%LOCALAPPDATA%\PlotJuggler\PlotJuggler4\mcp-instances\executable"
rem The app writes the path as UTF-8: read it in that code page.
chcp 65001 >nul
if exist "%REC%" set /p EXE=<"%REC%"
if defined EXE if exist "%EXE%" goto run
set "EXE=%ProgramFiles%\PlotJuggler4\bin\PlotJuggler4.exe"
if exist "%EXE%" goto run
:browser
set "RELAY=%~dp0..\relay\pj_mcp_relay.py"
where py >nul 2>nul && (py -3 "%RELAY%" & exit /b %errorlevel%)
where python >nul 2>nul && (python "%RELAY%" & exit /b %errorlevel%)
echo PlotJuggler 4 not found, and no Python for the browser version. Install PlotJuggler from https://plotjuggler.io and start it once, set PLOTJUGGLER4, or install Python 3. 1>&2
exit /b 1
:run
"%EXE%" --mcp-bridge
exit /b %errorlevel%
