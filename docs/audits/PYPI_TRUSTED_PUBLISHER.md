# PyPI *trusted publisher* — lo que falta configurar, y cómo comprobarlo

El intento de publicar `v2.0.0` falló aquí. El job `publicar-pypi` construyó y
verificó bien, llegó a `pypa/gh-action-pypi-publish` y PyPI lo rechazó con
**`invalid-publisher`**: PyPI no tenía registrado ningún *trusted publisher*
que coincidiera con quien pedía publicar.

Este documento existe para que quien tenga la cuenta lo configure **una vez y
bien**, y para que se pueda comprobar después que lo configuró para el sitio
correcto y no para otro parecido. Nada de aquí se ha ejecutado.

> **Este ciclo no configura PyPI y no relanza nada.** Los pasos de abajo son
> manuales y los da una persona con acceso al proyecto en PyPI.

---

## 1 · Los claims que hay que registrar

Un *trusted publisher* de GitHub Actions en PyPI se define con **cuatro campos**,
y son exactamente estos:

| Campo en el formulario de PyPI | Valor |
|---|---|
| **Owner** | `HorizunGroup` |
| **Repository name** | `horizun-pbi-mcp` |
| **Workflow name** | `release.yml` |
| **Environment name** | `pypi` |

Y el proyecto sobre el que se registra:

| | |
|---|---|
| **PyPI project name** | `horizun-pbi-mcp` |
| **Índice** | `pypi.org` — **no** TestPyPI |

De dónde sale cada uno, sin memoria de por medio:

- *Owner* y *Repository*: `github.repository` del run que falló →
  `HorizunGroup/horizun-pbi-mcp`.
- *Workflow*: el archivo es `.github/workflows/release.yml`; PyPI espera el
  **nombre del archivo**, no el `name:` de dentro (que es `release`).
- *Environment*: el job `publicar-pypi` declara `environment: pypi` en
  `.github/workflows/release.yml`. Si el formulario se deja en blanco, PyPI
  acepta el token **venga o no de ese environment**, que es una puerta más
  abierta de lo necesario: rellénalo.
- *Project name*: `pyproject.toml`, `name = "horizun-pbi-mcp"`.

### El `ref` del próximo intento

`refs/tags/v2.0.1`.

PyPI **no** configura el `ref`: el *trusted publisher* no filtra por tag. Quien
filtra es el workflow, con
`if: startsWith(github.ref, 'refs/tags/v')`. Se anota aquí para que, al
verificar el próximo run, se compruebe que el token que se emitió venía del tag
esperado y no de una rama.

### Lo que **no** hay que copiar

El error de OIDC muestra un claim `sub` con una forma parecida a
`repo:HorizunGroup/horizun-pbi-mcp:environment:pypi`. **No se copia ese `sub`
como configuración.** PyPI no pide un `sub`: pide los cuatro campos de arriba y
construye la comparación él mismo. Pegar la cadena entera en el campo
equivocado —típicamente en *Workflow name*— produce un publisher que no casa con
nada y el siguiente intento vuelve a fallar con el mismo `invalid-publisher`,
esta vez con la configuración ya hecha, que es peor porque parece resuelto.

---

## 2 · Procedimiento manual

1. Entrar en `pypi.org` con la cuenta que **posee** el proyecto
   `horizun-pbi-mcp`. Si el proyecto no existe todavía en PyPI bajo esa cuenta,
   hay que usar un *pending publisher* (mismo formulario, sección
   «publishing» del perfil) en vez del formulario del proyecto.
2. `Manage project` → `Publishing` → *Add a new publisher* → **GitHub**.
3. Rellenar los cuatro campos **exactamente** como en la tabla de arriba.
   Sin `https://`, sin `.git`, sin la ruta `.github/workflows/` delante del
   nombre del workflow, y respetando mayúsculas en `HorizunGroup`.
4. Guardar y **no publicar nada todavía**.
5. Configurar en GitHub el environment `pypi` con *required reviewers*, si aún
   no lo tiene. El environment es la puerta humana; el *trusted publisher* solo
   dice quién puede llamar.

---

## 3 · Evidencia que hay que guardar

| Qué | Cómo |
|---|---|
| El publisher registrado | Captura de la fila en `Manage project → Publishing`, con los cuatro campos legibles |
| Que es el proyecto correcto | La URL de esa página, que lleva el nombre del proyecto |
| Que el environment existe y tiene revisores | `gh api repos/HorizunGroup/horizun-pbi-mcp/environments/pypi` |
| El run que sí publicó | Id del run y `conclusion: success` del job `Publicar en PyPI el artefacto probado` |
| Que lo publicado es lo probado | El digest del wheel en PyPI comparado con el de `SHA256SUMS` del build — es **G6.1**, y no se cierra con que el job salga verde |

---

## 4 · Cómo comprobar que **no** se configuró otra cosa

Un publisher mal puesto no da error hasta el siguiente intento, y entonces el
error es el mismo de siempre y no dice en qué campo está la diferencia. Estas
cuatro comprobaciones separan los casos:

1. **¿Otro owner?** El formulario acepta cualquier cadena. `horizungroup` en
   minúsculas o una cuenta personal parecida se guardan sin protestar. Compara
   carácter a carácter con la salida de:

   ```bash
   gh api repos/HorizunGroup/horizun-pbi-mcp -q .full_name
   ```

2. **¿Otro repositorio?** Mismo riesgo con un fork o un repositorio de pruebas.
   La comparación es contra `full_name`, no contra lo que uno recuerda.

3. **¿Otro workflow?** Los valores que se cuelan son `release`, `ci.yml` y
   `.github/workflows/release.yml`. El correcto es `release.yml` y nada más.
   Los workflows que existen:

   ```bash
   gh api repos/HorizunGroup/horizun-pbi-mcp/actions/workflows -q '.workflows[].path'
   ```

4. **¿Otro environment, o ninguno?** Si el campo quedó vacío, cualquier job del
   workflow puede publicar, incluso uno añadido después sin pasar por la puerta
   humana. Tiene que decir `pypi`, y ese environment tiene que existir:

   ```bash
   gh api repos/HorizunGroup/horizun-pbi-mcp/environments -q '.environments[].name'
   ```

   El valor del workflow, para contrastar sin abrirlo a mano:

   ```bash
   python -c "import yaml;print(yaml.safe_load(open('.github/workflows/release.yml'))['jobs']['publicar-pypi']['environment'])"
   ```

**Y la comprobación que de verdad cierra el caso:** que el siguiente run del job
`publicar-pypi` termine en `success`. Mientras no exista ese run, esto está
configurado *según lo que dice el formulario*, no *según lo que hace PyPI*.

---

## 5 · Estado actual, fechado

| | |
|---|---|
| Fecha de esta lectura | **2026-08-15** |
| Último run del job | `95086392632`, `conclusion: failure`, 23:42:18Z → 23:42:36Z |
| Run completo | `31914746886`, tag `v2.0.0`, `head_sha 1f0405b` |
| Motivo | `invalid-publisher` |
| Configurado desde entonces | **no** — este ciclo no toca PyPI |
| Última versión en PyPI | **1.5.4** |

El detalle del intento fallido y lo que sí dejó demostrado está en
[`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md).
