@echo off
setlocal EnableExtensions
set "AQUI=%~dp0"
set "PYREAL="

rem El alias "python" de la Microsoft Store (WindowsApps) NO es Python: abre
rem la tienda o muere en silencio, y con el moria el plugin entero antes de
rem poder decir nada. Este lanzador resuelve un interprete REAL:
rem   1) el py launcher (viene con las instalaciones de python.org/winget);
rem   2) un python del PATH que NO sea el alias de WindowsApps;
rem   3) si no hay ninguno, un error legible por stderr con el remedio.

where py >nul 2>nul && set "PYREAL=py -3"
if defined PYREAL goto :lanzar

for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P| find /i "WindowsApps" >nul || if not defined PYREAL set PYREAL="%%P"
)
if defined PYREAL goto :lanzar

echo [horizun-pbi-mcp] No hay un Python real en esta maquina: solo el alias de la Microsoft Store, o ninguno. 1>&2
echo [horizun-pbi-mcp] Instalalo sin administrador:  winget install -e --id Python.Python.3.12 --scope user 1>&2
echo [horizun-pbi-mcp] O usa el instalador de un pegado (docs/INSTALL.md de HorizunGroup/horizun-pbi-mcp) y reinicia Claude. 1>&2
exit /b 1

:lanzar
%PYREAL% "%AQUI%plugin_launcher.py" %*
exit /b %errorlevel%
