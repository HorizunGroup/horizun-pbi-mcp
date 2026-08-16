# Plan de seguridad del remoto — preparado y **sin ejecutar**

Los siete ajustes que cierran G7.1, G7.3, G7.4 y G7.5, escritos como comandos
listos para pegar, cada uno con **cómo verificarlo** y **cómo deshacerlo**.

> **Ninguno de los comandos de la sección «Aplicar» se ha ejecutado.** Lo único
> que se ha corrido de este documento son las lecturas de la sección 1, que no
> modifican nada. Cambiar la configuración del remoto es una decisión humana y
> no se toma dentro de un ciclo de trabajo.

Requisito: `gh` autenticado con **admin** sobre el repositorio. Comprobado el
2026-08-15: `gh api repos/HorizunGroup/horizun-pbi-mcp -q .permissions` devuelve
`admin: true`, así que quien ejecute esto puede hacerlo.

---

## 1 · Estado leído el 2026-08-15 (solo lectura)

| Ajuste | Estado | Comando de lectura |
|---|---|---|
| Protección de `main` | **sin proteger** (`404 Branch not protected`) | `gh api repos/HorizunGroup/horizun-pbi-mcp/branches/main/protection` |
| Rulesets | **ninguno** (`[]`) | `gh api repos/HorizunGroup/horizun-pbi-mcp/rulesets` |
| Vulnerability alerts | **deshabilitado** (`404`) | `gh api -i repos/HorizunGroup/horizun-pbi-mcp/vulnerability-alerts` |
| Dependabot security updates | **deshabilitado** (`enabled: false`) | `gh api repos/HorizunGroup/horizun-pbi-mcp/automated-security-fixes` |
| Secret scanning | **deshabilitado** | `gh api repos/HorizunGroup/horizun-pbi-mcp -q .security_and_analysis` |
| Push protection | **deshabilitado** | el mismo |
| Private vulnerability reporting | **deshabilitado** (`enabled: false`) | `gh api repos/HorizunGroup/horizun-pbi-mcp/private-vulnerability-reporting` |

---

## 2 · Los nombres de los checks, **leídos y no inventados**

Es la parte que más fácil se estropea: exigir un check con un nombre que nadie
publica deja `main` bloqueada **para siempre**, esperando algo que no va a
llegar. Los PR no se pueden fusionar y el arreglo requiere volver a tocar la
protección.

Los nombres salen de los check-runs reales del commit `1f0405b`:

```bash
gh api "repos/HorizunGroup/horizun-pbi-mcp/commits/1f0405b4db9d132c1b7d994163b82f70c19a6b3a/check-runs" --paginate -q '.check_runs[] | "\(.name)\t\(.app.slug)\t\(.app.id)\t\(.conclusion)"'
```

Salida capturada:

| Check-run | Workflow | `app.id` | Conclusión | ¿Se exige? |
|---|---|---|---|---|
| `build` | `ci.yml` | 15368 | success | **sí** |
| `test (3.10)` | `ci.yml` | 15368 | success | **sí** |
| `test (3.13)` | `ci.yml` | 15368 | success | **sí** |
| `Analizar (python)` | `codeql.yml` | 15368 | success | **sí** |
| `Construir una sola vez` | `release.yml` | 15368 | success | **no** |
| `Probar los bytes construidos (3.10)` | `release.yml` | 15368 | success | **no** |
| `Probar los bytes construidos (3.13)` | `release.yml` | 15368 | success | **no** |
| `Publicar en PyPI el artefacto probado` | `release.yml` | 15368 | failure | **no** |
| `Publicar los metadatos en el MCP Registry` | `release.yml` | 15368 | skipped | **no** |

**Los cinco de `release.yml` no se exigen nunca.** Ese workflow solo se dispara
con `push` de un tag `v*`, así que en un pull request no existen: exigirlos
bloquearía todos los PR a la espera de checks que no se van a publicar. Este
commit los tiene porque es a la vez la punta de `main` **y** el objetivo del tag
`v2.0.0`; en un PR normal solo aparecen los cuatro primeros.

`app_id: 15368` es GitHub Actions. Va explícito porque `build` y `test` son
nombres genéricos: sin el `app_id`, cualquier integración de terceros que
publique un check llamado `build` satisfaría el requisito.

---

## 3 · Aplicar (**no ejecutado**)

### 3.1 · G7.1 — proteger `main`, exigir PR y los cuatro checks

Un solo `PUT` cubre protección, PR obligatorio, checks obligatorios y la
prohibición de force-push y de borrado — la API los expone juntos, y separarlos
en varias llamadas deja ventanas donde `main` está a medio proteger.

```bash
gh api -X PUT repos/HorizunGroup/horizun-pbi-mcp/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "build",             "app_id": 15368},
      {"context": "test (3.10)",       "app_id": 15368},
      {"context": "test (3.13)",       "app_id": 15368},
      {"context": "Analizar (python)", "app_id": 15368}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "required_linear_history": false,
  "lock_branch": false,
  "allow_fork_syncing": false
}
JSON
```

Notas sobre campos que no son obvios:

- `"restrictions": null` es **obligatorio** en el cuerpo aunque no se restrinja
  a nadie: omitirlo da `422`.
- `strict: true` exige que la rama esté al día con `main` antes de fusionar. Es
  lo que impide el clásico «los dos PR pasaban por separado y juntos rompen».
- `enforce_admins: true` se aplica **también a quien lo configura**. Es
  deliberado: una protección que el admin se salta protege del despiste ajeno y
  de ninguno propio. Si bloquea una emergencia, se desactiva a la vista de
  todos con el comando de *rollback* de abajo, y eso queda en el registro.
- `required_linear_history: false` porque el historial actual tiene merges de
  PR (`fa7ee0f` es uno).

**Verificar:**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/branches/main/protection -q '{checks: [.required_status_checks.checks[].context], strict: .required_status_checks.strict, admins: .enforce_admins.enabled, revisiones: .required_pull_request_reviews.required_approving_review_count, force_push: .allow_force_pushes.enabled, borrado: .allow_deletions.enabled}'
```

Esperado: los cuatro contextos exactos, `strict: true`, `admins: true`,
`revisiones: 1`, `force_push: false`, `borrado: false`.

**Verificar que de verdad bloquea** —y esto es lo que convierte el verde en
evidencia—: abrir un PR de prueba con un cambio trivial y comprobar que el botón
de fusionar está bloqueado hasta que los cuatro checks pasen; y que
`git push --force origin main` es **rechazado**.

**Rollback:**

```bash
gh api -X DELETE repos/HorizunGroup/horizun-pbi-mcp/branches/main/protection
```

Deja la rama exactamente como está hoy: sin protección.

---

### 3.2 · Vulnerability alerts (prerrequisito de G7.3)

Dependabot *security updates* no se puede activar sin esto.

```bash
gh api -X PUT repos/HorizunGroup/horizun-pbi-mcp/vulnerability-alerts
```

**Verificar** — `204` si está activo, `404` si no:

```bash
gh api -i repos/HorizunGroup/horizun-pbi-mcp/vulnerability-alerts | head -1
```

**Rollback:**

```bash
gh api -X DELETE repos/HorizunGroup/horizun-pbi-mcp/vulnerability-alerts
```

---

### 3.3 · G7.3 — Dependabot *security updates*

```bash
gh api -X PUT repos/HorizunGroup/horizun-pbi-mcp/automated-security-fixes
```

**Verificar:**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/automated-security-fixes
```

Esperado: `{"enabled": true, "paused": false}`.

**Rollback:**

```bash
gh api -X DELETE repos/HorizunGroup/horizun-pbi-mcp/automated-security-fixes
```

> Esto es la **otra mitad** del pineado por SHA. Un SHA no caduca ni se mueve, y
> por eso mismo tampoco se entera de una vulnerabilidad publicada: quien avisa
> es Dependabot. Pinear sin Dependabot es congelar el problema, no resolverlo.

---

### 3.4 · G7.4 — *secret scanning* y *push protection*

Los dos en una llamada: activar el escaneo sin la protección de push detecta el
secreto **después** de que ya esté en el historial, que es tarde.

```bash
gh api -X PATCH repos/HorizunGroup/horizun-pbi-mcp --input - <<'JSON'
{
  "security_and_analysis": {
    "secret_scanning": {"status": "enabled"},
    "secret_scanning_push_protection": {"status": "enabled"}
  }
}
JSON
```

**Verificar:**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp -q .security_and_analysis
```

Esperado: `secret_scanning` y `secret_scanning_push_protection` en `enabled`.

**Verificar que de verdad bloquea:** intentar empujar a una **rama desechable**
un archivo con un token de prueba de los que GitHub reconoce, y comprobar que el
push es rechazado. Borrar la rama después. No se hace sobre `main`.

**Rollback:** el mismo `PATCH` con `"status": "disabled"` en los dos.

---

### 3.5 · G7.5 — *private vulnerability reporting*

```bash
gh api -X PUT repos/HorizunGroup/horizun-pbi-mcp/private-vulnerability-reporting
```

**Verificar:**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/private-vulnerability-reporting
```

Esperado: `{"enabled": true}`.

**Rollback:**

```bash
gh api -X DELETE repos/HorizunGroup/horizun-pbi-mcp/private-vulnerability-reporting
```

> Sin esto, quien encuentre un fallo de seguridad solo tiene la vía pública: un
> issue abierto donde lo lea todo el mundo antes que nosotros. `SECURITY.md` ya
> pide reporte privado; esta opción es lo que hace que exista el canal.

---

### 3.6 · Environments de publicación con revisor humano

No es un gate, pero es la puerta que el workflow ya declara y que hoy no tiene
nadie detrás. Los tres environments —`pypi`, `mcp-registry` y
`github-release`— deben tener *required reviewers*.

`github-release` es **nuevo en v2.0.1** y GitHub lo crea solo, en el primer run,
**sin ninguna regla**. Declararlo en el workflow hace la puerta *posible*, no la
configura.

Se hace en la interfaz (`Settings → Environments`), o por API indicando el id
numérico de quien revisa:

```bash
gh api -X PUT repos/HorizunGroup/horizun-pbi-mcp/environments/github-release --input - <<'JSON'
{"reviewers": [{"type": "User", "id": 0}], "deployment_branch_policy": null}
JSON
```

El `id: 0` es un **marcador**: sustitúyelo por el real, que se obtiene con
`gh api users/<usuario> -q .id`. Tal cual, la llamada falla — a propósito, para
que nadie la pegue sin mirar.

**Verificar:**

```bash
gh api repos/HorizunGroup/horizun-pbi-mcp/environments -q '.environments[] | {name, reglas: [.protection_rules[].type]}'
```

Esperado: los tres con `required_reviewers` entre sus reglas.

**Rollback:** el mismo `PUT` con `"reviewers": []`.

---

## 4 · Orden recomendado, y por qué importa

1. **3.2** (vulnerability alerts) → **3.3** (Dependabot). El segundo depende del
   primero.
2. **3.4** y **3.5**, independientes entre sí.
3. **3.6**, antes que ningún intento de publicar.
4. **3.1** (proteger `main`) **el último**. Es el único que puede dejar el
   repositorio en un estado que estorbe al trabajo en curso, y conviene aplicarlo
   cuando no haya nada a medias.

## 5 · Lo que este plan **no** hace

- No configura PyPI. Eso es
  [`audits/PYPI_TRUSTED_PUBLISHER.md`](audits/PYPI_TRUSTED_PUBLISHER.md).
- No mueve, borra ni reutiliza el tag `v2.0.0`.
- No relanza ningún workflow ni publica nada.
- No cierra G7.1, G7.3, G7.4 ni G7.5 por sí mismo: los cierra **ejecutarlo** y
  guardar la salida JSON de cada verificación, fechada. Un plan escrito no es
  evidencia de nada, y este documento no se cuenta como tal en
  [`audits/CLASIFICACION_GATES.md`](audits/CLASIFICACION_GATES.md).
