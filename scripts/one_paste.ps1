# FUENTE CANONICA del bloque de un pegado. No lo copies a mano a ningun sitio.
#
# README.md, docs/INSTALL.md y skills/horizun-pbi-setup/SKILL.md incrustan ESTE
# archivo palabra por palabra, y tests/test_one_paste.py falla si alguno se
# desvia una coma. Un bloque que se mantiene en cuatro sitios acaba siendo
# cuatro bloques distintos, y el que se quede atras sera el que alguien pegue.
#
# Que sustituye: `irm .../main/scripts/instalar.ps1 | iex`, que descargaba de
# una RAMA -bytes que pueden cambiar bajo el mismo enlace- y ejecutaba lo que
# llegara sin mirarlo. HTTPS garantiza QUIEN te da los bytes, no QUE bytes.
#
# Que hace este, en orden: descarga a un temporal aleatorio desde una release
# FIJA, rechaza la respuesta si se anuncia mas grande de la cuenta, corta el
# stream si crece de la cuenta mientras baja, comprueba el SHA-256 y solo
# entonces ejecuta -con `&`, nunca con Invoke-Expression-. Si algo no cuadra,
# no se ejecuta nada y el temporal se borra igual.
#
# El SHA-256 se calcula con .NET y NO con `Get-FileHash`. `Get-FileHash` es un
# CMDLET: usarlo obliga a RESOLVERLO, y esa resolucion depende del estado del
# interprete -que modulos estan cargados, que dice PSModulePath, si la cache de
# analisis de modulos sirve, que hay en el perfil-. Nada de eso lo controla
# quien pega el bloque, y aqui se paga caro: si el comando no resuelve, `$real`
# se queda vacio y la unica comprobacion de integridad del bloque se apaga sola
# por el ambiente. Una verificacion que el entorno puede desactivar no es una
# verificacion. `[Security.Cryptography.SHA256]` es un TIPO de la BCL: lo
# resuelve el runtime, no el descubrimiento de comandos, y no hay ambiente que
# lo haga desaparecer. Se usa `::Create()` y no `SHA256Managed` porque en una
# maquina con FIPS activado la implementacion "managed" lanza.
#
# El SHA-256 y el tamano de abajo son los de scripts/instalar.ps1 y viven
# tambien en scripts/downloads_manifest.json; una prueba comprueba los tres
# contra los bytes reales del archivo.
$ErrorActionPreference = 'Stop'
$url = 'https://github.com/HorizunGroup/horizun-pbi-mcp/releases/download/v1.5.5/horizun-pbi-mcp-instalar.ps1'
$sha = '33fa1058d95445b97b7118d1c1a0fff9392d464f9bafdfdfc11dd069f970dad5'
$max = 131072
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('horizun-' + [guid]::NewGuid().ToString('N') + '.ps1')
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
    Write-Host ("SHA-256 verificado sobre " + $total + " bytes. Ejecutando el instalador...") -ForegroundColor Green
    & powershell -NoProfile -ExecutionPolicy Bypass -File $tmp
    if ($LASTEXITCODE -ne 0) {
        throw ("El instalador termino con codigo " + $LASTEXITCODE + ".")
    }
} catch {
    Write-Host ""
    Write-Host ("[ERROR] Instalacion abortada: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "        No se ejecuto nada que no coincidiera con el hash publicado." -ForegroundColor Red
    throw
} finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
