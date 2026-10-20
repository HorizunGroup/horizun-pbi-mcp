# Como se publica una release de Horizun PBI MCP

Este documento es el procedimiento exacto. Complementa `docs/RELEASE_CHECKLIST.md`
(las comprobaciones previas a etiquetar) y explica la parte que cambia con la
firma: **el instalador se firma en el arbol, antes de etiquetar**.

## Resumen

1. Rama `release/X.Y.Z`: version en todos los sitios, `CHANGELOG.md`,
   `RELEASE_NOTES_X.Y.Z.md`.
2. **Firmar `scripts/instalar.ps1` con la clave Certum, en el arbol**
   (SimplySign Desktop conectado).
3. **Recalcular los pines** del instalador a partir del archivo firmado
   (`python scripts/pin_instalador.py`).
4. PR, CI en verde, merge, tag anotado `vX.Y.Z`.
5. La CI construye, prueba, publica en PyPI y en el MCP Registry y deja la
   GitHub Release como **borrador**.
6. El job `firmar-y-publicar` queda **esperando la aprobacion de Pablo**
   (environment `firma-certum`). Con SimplySign Desktop conectado, se aprueba
   en la pestana Actions: el job verifica las firmas y publica el borrador.

## Por que el instalador se firma en el arbol

El unico binario con firma de este repositorio es el instalador
`scripts/instalar.ps1` (asset `horizun-pbi-mcp-instalar.ps1`, copia byte a byte).
Su **SHA-256 esta fijado** en cuatro sitios del repo:

| Sitio | Para que |
| --- | --- |
| `scripts/downloads_manifest.json` (`sha256`, `actual_bytes`, `url`) | fuente del release (`release_build`, `release_verify`, `release_publish`) |
| `scripts/one_paste.ps1` (`$sha`, `$url`) | bloque de un pegado; **verifica el hash antes de ejecutar** |
| `docs/INSTALL.md` | copia del bloque (`sync_one_paste.py`) |
| `skills/horizun-pbi-setup/SKILL.md` | copia del bloque (`sync_one_paste.py`) |

Firmar el `.ps1` **despues** de construir la release (re-firmar el asset en el
borrador) cambia sus bytes y por tanto su hash: el one-paste, que compara con el
hash fijado en el repo, rechazaria el instalador publicado. Eso es exactamente lo
que paso con la **v2.1.2**: el asset publicado se re-firmo a posteriori
(45 966 bytes, SHA-256 `5f608ad5...9360`), mientras el repo y el bloque de un
pegado siguen fijando el instalador sin firmar (31 600 bytes, SHA-256
`f01c2f0d...eb8`). Como las releases publicadas son inmutables, esa
discrepancia no se puede corregir en la v2.1.2; queda resuelta desde la v2.1.3.

Diseno vigente:

* el instalador se firma **en el arbol**, antes de etiquetar. El bloque
  `# SIG # Begin signature block ... # SIG # End signature block` se anade al
  final del archivo;
* los pines (`sha256`, `actual_bytes`) se calculan sobre el archivo **firmado**
  (`scripts/pin_instalador.py`);
* el pipeline **exige** el instalador firmado y con el hash del archivo firmado:
  `release_build.py` para sin construir nada si no hay firma, `release_verify.py`
  lo comprueba (y, con `--authenticode`, Windows valida la firma) y
  `release_publish.py` compara el asset del borrador con el manifest;
* `firmar-y-publicar` usa `-ExigirScripts`: **no firma** el `.ps1` (cambiaria el
  hash), **exige** que ya venga firmado y lo verifica antes de publicar.
* `.gitattributes` marca `scripts/instalar.ps1` como `-text`: git no puede
  normalizar ni un byte de lo que cubre el hash (el bloque de firma trae CRLF,
  y signtool intercala un CRLF entre el cuerpo y el bloque; el resto del cuerpo
  es LF puro, ASCII y sin BOM).

PyPI y el MCP Registry **no esperan** a la firma: el wheel y el sdist no llevan
binarios propios ni cambian por firmar el instalador.

## Firma del instalador (paso a paso)

Requisitos: SimplySign Desktop **conectado** con la cuenta de Pablo (el
certificado de HORIZUN GROUP S.A.S., huella
`D19F95C700EFEB78F03B6A2F9F6FBF87BF9BF8FA`, debe estar en
`Cert:\CurrentUser\My` **con clave privada**) y la rama `release/X.Y.Z` con la
version ya subida en `scripts/instalar.ps1` (el `ref` del marketplace de Codex)
y el arbol limpio.

```powershell
# 0. Que se puede firmar (debe decir True) y que el instalador NO esta firmado aun.
(Get-Item Cert:\CurrentUser\My\D19F95C700EFEB78F03B6A2F9F6FBF87BF9BF8FA).HasPrivateKey
git status --short                       # vacio, salvo lo que se esta preparando

# 1. FIRMAR EN EL ARBOL (Authenticode, sello de tiempo de Certum).
& "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" sign /sha1 D19F95C700EFEB78F03B6A2F9F6FBF87BF9BF8FA /fd sha256 /tr http://time.certum.pl /td sha256 scripts/instalar.ps1

# 2. Comprobar la firma: Valid, emisor Certum, sello de tiempo Certum.
$s = Get-AuthenticodeSignature scripts/instalar.ps1
$s.Status; $s.SignerCertificate.Subject; $s.SignerCertificate.Issuer; $s.TimeStamperCertificate.Subject
& "C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" verify /pa scripts/instalar.ps1

# 3. RECALCULAR LOS PINES a partir del archivo firmado (manifest, one_paste.ps1,
#    INSTALL.md y la skill). Se niega a pinear un instalador sin firmar.
python scripts/pin_instalador.py
python scripts/pin_instalador.py --check          # debe decir OK

# 4. Lo que cambia en el commit: SOLO el instalador y sus pines.
git status --short
git diff --stat
```

Reglas mientras el archivo esta firmado:

* **No edites, no abras para guardar, no reformatees** `scripts/instalar.ps1`
  despues de firmar. Cualquier cambio invalida la firma (`HashMismatch`) y hay que
  volver al paso 1 **y al 3**.
* Si hay que cambiar algo del instalador: cambiarlo, quitar el bloque de firma
  anterior (o `git checkout` del archivo sin firmar), firmar de nuevo y
  recalcular los pines.
* No uses `git add --renormalize` ni cambies `.gitattributes`.

Comprobaciones antes de empujar (todas deben pasar):

```powershell
python -m pytest -q                                              # toda la suite
python scripts/release_build.py --outdir artefactos              # exige firma + hash
python scripts/release_verify.py --dir artefactos --expect-version vX.Y.Z --authenticode
git show HEAD:scripts/instalar.ps1 | python -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"
#   ^ debe ser el sha256 de scripts/downloads_manifest.json
```

Con el instalador **sin firmar**, estas comprobaciones fallan de forma explicita
(no hay ningun modo de saltarlas):

* `release_build.py`: `[release_verify] scripts/instalar.ps1 NO ESTA FIRMADO ...`
  antes de construir nada;
* `release_verify.py`: lo mismo sobre el asset;
* `scripts/pin_instalador.py`: se niega a fijar el hash;
* pruebas: `tests/test_firma_instalador.py::test_el_instalador_del_arbol_esta_firmado`
  y las de `test_release_*` que construyen o publican el artefacto.

## Publicar

```powershell
# Tras el merge del PR (los bytes firmados NO cambian al fusionar: -text).
git switch main; git pull
git show main:scripts/instalar.ps1 | python -c "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())"   # == manifest
git tag -a vX.Y.Z -m "Horizun PBI MCP X.Y.Z"
git push origin vX.Y.Z
```

El tag dispara `.github/workflows/release.yml`:

`build` -> `test` (3.10, 3.13) -> `publicar-pypi` -> `publicar-mcp` ->
`publicar-github-release` (crea el **borrador**, relee cada asset por la API y
compara el instalador con el manifest) -> `firmar-y-publicar` (**espera
aprobacion**).

### Aprobar la firma

1. La CI deja el borrador y el job `firmar-y-publicar` queda en *Waiting*.
2. Aviso a Pablo (revisor obligatorio del environment `firma-certum`).
3. Pablo **conecta SimplySign Desktop** (la clave Certum debe responder).
4. En GitHub: *Actions* -> el run del tag -> **Review deployments** -> marcar
   `firma-certum` -> **Approve and deploy**.
5. El job, en el runner `horizun-sign`: comprueba que puede firmar (firma un
   archivo de prueba), descarga los assets del borrador, firma los binarios
   propios si los hubiera (aqui no hay), **exige** que `horizun-pbi-mcp-instalar.ps1`
   venga firmado (`-ExigirScripts`: firma Valid, huella de HORIZUN GROUP, emisor y
   sello de tiempo de Certum), regenera y compara hashes, sube lo que cambie
   (nada, normalmente), relee y **publica el borrador**. Si algo falla, **no
   publica**.

### Despues de publicar

```powershell
gh release view vX.Y.Z --repo HorizunGroup/horizun-pbi-mcp --json isDraft,assets
gh release download vX.Y.Z --repo HorizunGroup/horizun-pbi-mcp -p horizun-pbi-mcp-instalar.ps1 -D $env:TEMP\hz
(Get-FileHash $env:TEMP\hz\horizun-pbi-mcp-instalar.ps1).Hash   # == sha256 del manifest
(Get-AuthenticodeSignature $env:TEMP\hz\horizun-pbi-mcp-instalar.ps1).Status   # Valid
```

Y pegar el bloque de un pegado de `docs/INSTALL.md` en una consola de prueba.

## El runner de firma

* Un runner self-hosted con las etiquetas `self-hosted, windows, horizun-sign`
  por repositorio, en `C:\Users\pablo\.horizun\sign-runners\horizun-pbi-mcp`.
* Lo arranca la tarea programada **Horizun Sign Runners**, que ejecuta
  `C:\Users\pablo\.horizun\sign-runners\iniciar-runners.ps1`. Para arrancarlo o
  revisarlo a mano:

  ```powershell
  Get-ScheduledTask -TaskName 'Horizun Sign Runners' | Get-ScheduledTaskInfo
  & C:\Users\pablo\.horizun\sign-runners\iniciar-runners.ps1
  gh api repos/HorizunGroup/horizun-pbi-mcp/actions/runners --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
  ```

  Debe aparecer `status: online`. Si esta `offline`, ejecutar el script de
  arranque y volver a lanzar el job.

## Si el job de firma falla por SimplySign desconectado

El script comprueba que puede firmar **antes de tocar la release** y falla con
`No se puede firmar (SimplySign desconectado o sesion caducada)`. La release
sigue en borrador y no se ha subido nada.

1. Reconectar SimplySign Desktop con la cuenta del runner (autenticar de nuevo).
2. En la pestana Actions del run: **Re-run failed jobs** y aprobar de nuevo el
   environment `firma-certum` si lo pide.

## Si algo de los bytes publicados hay que corregirlo

Las releases publicadas son **inmutables**: no se reemplazan assets. Cualquier
correccion es una **version patch nueva** (misma secuencia: rama, firma en el
arbol, pines, PR, tag). Si el *borrador* esta mal y aun no se publico, se puede
borrar el borrador y el tag (`gh release delete vX.Y.Z --cleanup-tag`), corregir y
repetir; nunca despues de publicar.

## Que verifica cada herramienta

| Herramienta | Que exige |
| --- | --- |
| `scripts/pin_instalador.py [--check]` | instalador firmado; manifest, `one_paste.ps1`, INSTALL.md y skill con su hash/tamano |
| `scripts/release_build.py` | instalador firmado (primer paso) y con el hash del manifest |
| `scripts/release_verify.py [--authenticode]` | digests de `SHA256SUMS`; asset firmado, con CUERPO ASCII/LF/sin BOM (solo el bloque de firma admite CRLF) y el hash del manifest; version en todos los sitios; con `--authenticode`, `Get-AuthenticodeSignature` Valid, huella y Certum |
| `scripts/release_publish.py` | borrador; sube exactamente lo firmado en `SHA256SUMS`; relee por la API y compara; el instalador remoto == manifest y firmado |
| `scripts/firmar-release.ps1 -ExigirScripts` | firma Valid de HORIZUN GROUP con sello Certum en el `.ps1`; publica el borrador solo si todo cuadra |
