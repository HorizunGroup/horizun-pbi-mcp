---
name: horizun-pbi-setup
description: Instala, actualiza, repara y verifica el runtime local de Horizun PBI MCP cuando se usa como plugin de Codex o Claude. Úsala si el servidor solo muestra pbi_install_runtime/pbi_install_status, si faltan dependencias, DLL o esquemas, si el plugin no arranca, o si el usuario pide instalar o diagnosticar el plugin.
---

# Instalar Horizun PBI MCP

El plugin no incluye binarios propietarios. Prepara un entorno Python aislado
y descarga las DLL y esquemas fijados, verificando sus hashes. **La meta es
que la persona no pelee con dependencias: las resuelves TÚ**, con el
instalador de un pegado y los remedios de esta skill.

## El prompt de instalación (lo único que la persona pega)

Cuando alguien diga "instálame el MCP de Power BI" (o llegue con este prompt),
este es el runbook completo. Todo es a nivel usuario; nunca pidas
administrador.

1. **Corre el instalador de un pegado** en PowerShell:
   El bloque exacto esta en `scripts/one_paste.ps1` y se reproduce
   integro en `README.md` y `docs/INSTALL.md`. **No lo escribas de
   memoria ni lo acortes**: descarga desde una release fija,
   comprueba el SHA-256 y solo entonces ejecuta.

```powershell
$ErrorActionPreference = 'Stop'
$url = 'https://github.com/HorizunGroup/horizun-pbi-mcp/releases/download/v1.5.5/horizun-pbi-mcp-instalar.ps1'
$sha = '33fa1058d95445b97b7118d1c1a0fff9392d464f9bafdfdfc11dd069f970dad5'
$max = 131072
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('horizun-' + [guid]::NewGuid().ToString('N') + '.ps1')
# En que punto se quedo, para que el mensaje final diga la verdad y no una
# formula. Antes, un instalador que se ejecutaba y devolvia error terminaba
# imprimiendo "No se ejecuto nada que no coincidiera con el hash publicado":
# cierto en lo literal y enganoso en lo que la persona entiende, que es que no
# se ejecuto nada.
$fase = 'descarga'
$ejecutado = $false
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $peticion = [Net.HttpWebRequest]::Create($url)
    $peticion.UserAgent = 'horizun-pbi-mcp-one-paste'
    $peticion.Timeout = 60000
    $respuesta = $peticion.GetResponse()
    if ($respuesta.ContentLength -gt $max) {
        throw ("El servidor anuncia " + $respuesta.ContentLength + " bytes y el maximo aceptado es " + $max + ". No se descarga nada.")
    }
    $entrada = $respuesta.GetResponseStream()
    $salida = [IO.File]::Open($tmp, 'Create', 'Write', 'None')
    $total = 0
    try {
        $bloque = New-Object byte[] 8192
        while (($leidos = $entrada.Read($bloque, 0, $bloque.Length)) -gt 0) {
            $total += $leidos
            if ($total -gt $max) {
                throw ("La descarga supero " + $max + " bytes mientras bajaba. Se aborta sin ejecutar nada.")
            }
            $salida.Write($bloque, 0, $leidos)
        }
    } finally {
        $salida.Dispose(); $entrada.Dispose(); $respuesta.Dispose()
    }
    if ($total -eq 0) { throw "La descarga llego vacia. No se ejecuta nada." }
    $fase = 'verificacion'
    if ($sha -cnotmatch '^[0-9a-f]{64}$') {
        throw "El hash publicado en el bloque no es un SHA-256 de 64 hex en minusculas. No se ejecuta nada."
    }
    $flujo = [IO.File]::Open($tmp, 'Open', 'Read', 'Read')
    try {
        $algoritmo = [Security.Cryptography.SHA256]::Create()
        try { $digest = $algoritmo.ComputeHash($flujo) } finally { $algoritmo.Dispose() }
    } finally { $flujo.Dispose() }
    $real = [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
    if ($real.Length -ne 64) {
        throw "No se pudo calcular el SHA-256 de lo descargado. No se ejecuta nada."
    }
    if ($real -ne $sha) {
        throw ("SHA-256 NO coincide. Esperado " + $sha + ", recibido " + $real + ". No se ejecuta nada.")
    }
    $fase = 'ejecucion'
    Write-Host ("SHA-256 verificado sobre " + $total + " bytes. Ejecutando el instalador...") -ForegroundColor Green
    $ps = [IO.Path]::Combine($PSHOME, 'powershell.exe')
    if (-not [IO.File]::Exists($ps)) {
        $ps = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    }
    if (-not [IO.File]::Exists($ps)) {
        throw "No se encontro Windows PowerShell. No se ejecuta nada."
    }
    & $ps -NoProfile -ExecutionPolicy Bypass -File $tmp
    $ejecutado = $true
    if ($LASTEXITCODE -ne 0) {
        throw ("El instalador termino con codigo " + $LASTEXITCODE + ".")
    }
} catch {
    Write-Host ""
    Write-Host ("[ERROR] Instalacion abortada: " + $_.Exception.Message) -ForegroundColor Red
    if ($ejecutado) {
        Write-Host "        El instalador SI llego a ejecutarse: sus bytes coincidian con el hash publicado, y fallo durante la instalacion." -ForegroundColor Red
    } else {
        Write-Host ("        No se ejecuto nada: se aborto en la fase '" + $fase + "', antes de lanzar el instalador.") -ForegroundColor Red
    }
    throw
} finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
```

   Instala Python real, Git, Node (opcional), ajusta la política de ejecución
   del usuario y registra el plugin. Es idempotente: repetirlo es seguro.
2. **Atiende sus `[PENDIENTE]`**: cada uno trae el remedio o el id exacto de
   paquete user-scope para pedirle a TI. Resuélvelos tú cuando sea posible
   (p. ej. reabrir la terminal por PATH viejo) y repite el paso 1.
3. Con el plugin registrado, la instalación del runtime empieza sola al
   cargar el plugin. Llama `pbi_install_status` para ver el avance.
4. Si terminó en `failed`, llama `pbi_install_runtime` UNA vez: relanzar
   REANUDA desde el paso caído (las descargas van verificadas por hash y los
   pasos de red reintentan solos 3 veces).
5. En `ready`, pide reiniciar Codex o Claude. Al volver, comprueba que
   `tools/list` expone las tools `pbi_*` completas, no solo las dos de
   instalación.
6. Si falla, informa el paso y el mensaje del status. No borres el runtime ni
   ocultes el error.

## Síntomas de campo y su remedio (medidos en sesión real, 2026-08-12)

- **El plugin no aparece ni da error** → `python` era el alias de la
  Microsoft Store. Desde 1.5.1 el lanzador (`launch.cmd`) lo esquiva solo
  (`py -3` primero); si no hay NINGÚN Python real, su stderr trae el comando
  (`winget install -e --id Python.Python.3.12 --scope user`).
- **"Git is required for local sessions"** → lo exige Claude Code:
  `winget install -e --id Git.Git --scope user`.
- **"running scripts is disabled"** →
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force`.
- **Comando recién instalado "no reconocido"** → PATH viejo en esa terminal:
  ciérrala y ábrela de nuevo (o reinicia el editor completo).
- **Descarga muere a mitad** → carrera DNS IPv6 conocida contra
  nuget.org/developer.microsoft.com; ya se reintenta solo. Si agota los
  intentos, relanzar reanuda: nada descargado se repite.
- **Sin permisos ni para winget** → entrega a TI la lista que imprime el
  instalador (`Python.Python.3.12`, `Git.Git`, `OpenJS.NodeJS.LTS`, todos
  user-scope). Esa impresión ES el ticket.

## Límites que debes explicar

- No hay un `.exe` propio ni hace falta registrar manualmente un servidor MCP.
- Sí se requiere Windows, Power BI Desktop y Python 3.10 o posterior. Un MCP
  remoto no puede controlar el Desktop ni los archivos PBIP locales.
- Node 20 es opcional: solo añade el validador oficial de informes PBIR.
- Nunca copies DLL, credenciales, `.pbix` o `.pbip` dentro del plugin.
- Cuenta compartida del equipo: funciona, pero avisa del riesgo de
  desactivación temporal o permanente.
