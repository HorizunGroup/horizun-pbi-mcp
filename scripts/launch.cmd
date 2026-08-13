@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "AQUI=%~dp0"
set "PYREAL="
set "VIEJO="

rem Este fichero existe porque el plugin moria MUDO cuando la maquina no tenia
rem un Python utilizable, y nadie sabia por que. Comprobar el NOMBRE del
rem candidato no basta; hay tres formas medidas de tener "python" y no tenerlo:
rem
rem   1) el alias de la Microsoft Store (WindowsApps): abre la tienda o muere
rem      en silencio, pero aparece en el PATH como si fuera Python;
rem   2) un py.exe HUERFANO (alguien desinstalo Python y el lanzador quedo):
rem      existe, supera cualquier prueba de presencia, y luego revienta con
rem      "Python 3.x not found" y codigo 103 sin decir el remedio;
rem   3) un Python 3.9 o anterior: arranca de sobra para fallar mas tarde y
rem      peor, ya dentro del servidor, con un error que no menciona la version.
rem
rem Por eso aqui no se pregunta si un candidato EXISTE, sino si CORRE y si
rem cumple el piso declarado en pyproject.toml (3.10). Se usa el primero que
rem pasa la prueba; los que fallan se descartan sin ruido, y si NINGUNO pasa se
rem distingue "no hay Python" de "lo hay pero es viejo", porque el remedio de
rem cada caso es distinto.
set "PRUEBA=import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 9)"

rem 1) el py launcher (viene con las instalaciones de python.org / winget)
call :probar py -3

rem 2) cualquier python del PATH que no sea el alias de la Store
for /f "delims=" %%P in ('where python 2^>nul') do (
  echo %%P| find /i "WindowsApps" >nul || call :probar "%%P"
)

rem 3) instalaciones que el PATH de ESTA consola todavia no ve. Es el caso de
rem    campo "lo acabo de instalar y no lo reconoce": winget dejo Python en su
rem    sitio, pero la terminal abierta conserva el PATH viejo. En vez de pedirle
rem    a la persona que cierre y abra ventanas, se mira donde winget instala.
call :probar_carpeta "%LOCALAPPDATA%\Programs\Python\Python3*"
call :probar_carpeta "%LOCALAPPDATA%\Python\pythoncore-3*"
call :probar_carpeta "%ProgramFiles%\Python3*"

if defined PYREAL goto :lanzar

if defined VIEJO (
  echo [horizun-pbi-mcp] El unico Python de esta maquina es anterior a 3.10, y el servidor necesita 3.10 o superior. 1>&2
  echo [horizun-pbi-mcp] Instala uno nuevo sin administrador:  winget install -e --id Python.Python.3.12 --scope user 1>&2
  echo [horizun-pbi-mcp] Convive con el que ya tienes; despues reinicia Claude. 1>&2
  exit /b 1
)
echo [horizun-pbi-mcp] No hay un Python real y utilizable aqui: solo el alias de la Microsoft Store, un py.exe huerfano, o ninguno. 1>&2
echo [horizun-pbi-mcp] Instalalo sin administrador:  winget install -e --id Python.Python.3.12 --scope user 1>&2
echo [horizun-pbi-mcp] O usa el instalador de un pegado (docs/INSTALL.md de HorizunGroup/horizun-pbi-mcp) y reinicia Claude. 1>&2
exit /b 1

:probar_carpeta
if defined PYREAL exit /b 0
for /d %%D in (%1) do (
  if not defined PYREAL call :probar "%%D\python.exe"
)
exit /b 0

:probar
if defined PYREAL exit /b 0
rem El CALL no es decorativo: si el candidato es un .bat/.cmd (los shims de
rem pyenv-win y los wrappers corporativos lo son), invocarlo sin call TRANSFIERE
rem el control y no vuelve nunca -- el lanzador moria ahi, mudo, en el sitio
rem exacto que existe para evitarlo. Con call vuelve y trae su codigo de salida.
call %* -c "%PRUEBA%" >nul 2>nul
if "%errorlevel%"=="0" (
  set "PYREAL=%*"
  exit /b 0
)
rem 9 = corrio, pero es anterior a 3.10. Cualquier otro codigo (incluido 9009,
rem "no se reconoce") significa que ese candidato no es un Python utilizable.
if "%errorlevel%"=="9" set "VIEJO=1"
exit /b 0

:lanzar
call %PYREAL% "%AQUI%plugin_launcher.py" %*
exit /b %errorlevel%
