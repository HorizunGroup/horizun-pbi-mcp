# Evidencia remota del 2026-08-15 — el intento fallido de v2.0.0

Este documento existe porque dos gates que llevaban meses etiquetados como
«imposibles sin una release publicada» resultaron **cosechables sin publicar
nada**: el intento fallido de `v2.0.0` produjo, en el remoto de verdad,
exactamente la observación que pedían.

Todo lo de aquí es **lectura**. Ni un comando de este documento muta el remoto,
y todos son reproducibles por cualquiera con acceso de lectura al repositorio.

> **NOTA POSTERIOR — 2026-08-16: el tag `v2.0.0` fue BORRADO del remoto.** Lo
> que sigue en este documento se capturó el 2026-08-15, cuando el tag existía, y
> se conserva tal cual porque es el registro de esa observación. La regla que se
> escribió aquí abajo —que un tag público no se mueve ni se borra— se revocó a
> conciencia para este tag concreto, no se incumplió por descuido: vivió unas
> dieciocho horas, nunca se publicó nada bajo él, y el commit al que apuntaba
> (`1f0405b`) sigue alcanzable desde `main`, así que nadie puede encontrar bytes
> distintos bajo un nombre que ya se hubiera traído. El motivo del borrado fue
> de presentación: un tag sin release al que no lleva ningún camino. El razonado
> completo está en el `CHANGELOG`, entrada `2.0.1`.

> **El tag `v2.0.0` es inmutable.** Existe en el remoto y apunta a `1f0405b`,
> pero se creó durante un intento de publicación fallido y **no se publicó nada**
> bajo él: ni GitHub Release, ni PyPI, ni MCP Registry. Que PyPI no llegara a
> publicar **no** lo convierte en un tag libre: un tag público puede haber sido
> descargado por terceros durante ese intervalo. No se mueve, no se borra y no
> se reutiliza. La corrección se entrega en `v2.0.1`.

---

## 1 · Estado del remoto en el momento de la captura

| Hecho | Valor |
|---|---|
| `main` | `1f0405b4db9d132c1b7d994163b82f70c19a6b3a` |
| Tag `v2.0.0` | existía, apuntaba a `1f0405b` — borrado el 2026-08-16 |
| GitHub Releases | la última sigue siendo **v1.5.4** |
| PyPI | la última sigue siendo **1.5.4** |
| MCP Registry | la última sigue siendo **1.5.4** |

Reproducible el 2026-08-15 con el comando de abajo. **Desde el borrado del
2026-08-16 ya no reproduce**: devuelve `Not Found`, que es la respuesta correcta
y no una contradicción de lo que este documento registró.

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/git/ref/tags/v2.0.0 -q .object.sha
```

---

## 2 · G7.2 — CodeQL en verde sobre `main` / `1f0405b`

**Lo que el gate pedía:** CodeQL ejecutándose y terminando en verde en el
remoto, no un workflow escrito y nunca observado.

**Comando (lectura):**

```bash
gh api "repos/HorizunGroup/horizun-pbi-mcp/actions/workflows/codeql.yml/runs?head_sha=1f0405b4db9d132c1b7d994163b82f70c19a6b3a" -q '.workflow_runs[] | {id,name,event,status,conclusion,head_branch,head_sha,created_at}'
```

**Salida capturada el 2026-08-15:**

```json
{
  "id": 31913970370,
  "name": "codeql",
  "event": "push",
  "status": "completed",
  "conclusion": "success",
  "head_branch": "main",
  "head_sha": "1f0405b4db9d132c1b7d994163b82f70c19a6b3a",
  "created_at": "2026-08-15T23:08:48Z"
}
```

**El check-run concreto**, que es el nombre que hay que exigir en la protección
de rama:

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/check-runs/95083064047 -q '{name,conclusion,head_sha,started_at,completed_at}'
```

```json
{
  "name": "Analizar (python)",
  "conclusion": "success",
  "head_sha": "1f0405b4db9d132c1b7d994163b82f70c19a6b3a",
  "started_at": "2026-08-15T23:08:51Z",
  "completed_at": "2026-08-15T23:10:56Z"
}
```

**Qué NO demuestra.** Que CodeQL pase no dice que el repositorio esté libre de
los defectos que CodeQL no busca, y no cierra ninguno de los otros cuatro gates
de G7: la protección de `main`, Dependabot *security updates*, *secret
scanning* con *push protection* y *private vulnerability reporting* siguen
**deshabilitados** en el remoto, comprobado el mismo día. El plan para
activarlos —comandos preparados y **no ejecutados**— está en
[`../PLAN_SEGURIDAD_GITHUB.md`](../PLAN_SEGURIDAD_GITHUB.md).

---

## 3 · G6.2 — un fallo aguas arriba detiene la publicación por `needs`

**Lo que el gate pedía:** comprobar que un tag con la cadena en rojo **no
publica**. Estaba clasificado como externo porque parecía exigir publicar una
release para poder observarlo.

**Lo que pasó de verdad.** El run de release del tag `v2.0.0` construyó y probó
correctamente, `publicar-pypi` **falló** con `invalid-publisher` —el *trusted
publisher* de PyPI no estaba configurado— y `publicar-mcp` quedó **omitido**
sin ejecutar un solo paso, porque lo ata `needs: [build, test, publicar-pypi]`.

**Comando (lectura):**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/actions/runs/31914746886/jobs -q '.jobs[] | "\(.name)\t\(.status)\t\(.conclusion)"'
```

**Salida capturada el 2026-08-15:**

```
Construir una sola vez                        completed  success
Probar los bytes construidos (3.13)           completed  success
Probar los bytes construidos (3.10)           completed  success
Publicar en PyPI el artefacto probado         completed  failure
Publicar los metadatos en el MCP Registry     completed  skipped
```

Con el run: `id 31914746886`, `event: push`, `head_branch: v2.0.0`,
`head_sha: 1f0405b…`, `conclusion: failure`.

**Precisión sobre lo que esto demuestra, y lo que no.** Lo observado es que un
**job aguas arriba en rojo deja al siguiente sin ejecutarse**: el mecanismo
`needs` funcionando sobre el remoto real, con evidencia fechada, y no un
simulacro. Lo que aquí falló fue `publicar-pypi`, **no la suite**: `test` pasó
en 3.10 y en 3.13. Es el mismo mecanismo y la misma arista del DAG —`test` y
`publicar-pypi` están los dos en el `needs` de `publicar-mcp`—, pero conviene
decirlo con las palabras exactas: *no se empujó un tag con la suite en rojo*.
Que la suite en rojo detenga la publicación está cubierto además por mutación
en `tests/test_release_pipeline.py`, donde quitar `needs: test` a un publicador
enciende su guarda.

**El efecto colateral que sí valió la pena.** Ese mismo run dejó a la vista un
defecto que ninguna prueba veía: aunque PyPI y el registro hubieran funcionado,
**nadie creaba la GitHub Release**, y el instalador de un pegado descarga de
`releases/download/v<version>/…`. Es RELEASE-004, y se corrige en `v2.0.1`.

---

## 4 · Lo que este documento no cierra

| Gate | Por qué sigue pendiente |
|---|---|
| G6.1 | No existe ninguna versión 2.x en PyPI. El intento falló antes de subir nada |
| G6.4 | El asset `horizun-pbi-mcp-instalar.ps1` no existe en ninguna release. `scripts/downloads_manifest.json` sigue en `status: pending_remote_release` y una prueba lo vigila |
| G7.1, G7.3, G7.4, G7.5 | Configuración del remoto, comprobada como **deshabilitada** el 2026-08-15. Requieren admin y una decisión humana |

**Un tag no es una release publicada.** Que `v2.0.0` exista no cierra G6.1 ni
G6.4, y no se cuenta como tal en
[`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md).
