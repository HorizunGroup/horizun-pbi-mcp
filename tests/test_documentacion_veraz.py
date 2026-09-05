"""DOC-001, DOC-002 y DOC-003 — la documentación contradice al producto.

Los tres son el mismo defecto de forma: **un documento que promete algo que el
código rechaza**. No es un problema de estilo. Quien lee el README es una
persona que va a escribir esa llamada, o un LLM que la va a construir; y en los
dos casos el resultado es una llamada que siempre falla y una tarde perdida
buscando por qué.

  - **DOC-001.** `mode="both"` se ofrece en la tabla de capacidades y hasta se
    pone de EJEMPLO —«format `0.0%`, mode both»— cincuenta líneas antes de que
    el mismo README lo declare bloqueado. `services/dual_mode.py` lo rechaza
    antes de tocar nada.
  - **DOC-002.** `AGENTS.md` afirma que de este repositorio no se publica nada a
    PyPI. `.github/workflows/release.yml` publica a PyPI por tag.
  - **DOC-003.** El README promete que funciona en un «PC completamente vacío»
    en 10–20 minutos. El instalador trae Python, Node y Claude Code; **no trae
    Power BI Desktop**, y sin él no hay modo LIVE, ni captura, ni validación de
    render.

Estas pruebas no opinan sobre cómo redactar. Comprueban que el documento no diga
lo contrario de lo que hace el código.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def test_la_arquitectura_describe_el_producto_actual():
    """La arquitectura vieja sobrevivio de 34 a 134 tools sin que nadie fallara."""
    texto = (RAIZ / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    golden = __import__("json").loads(
        (RAIZ / "tests" / "golden" / "tools_v1.json").read_text(encoding="utf-8"))
    assert str(golden["tool_count"]) in texto
    assert "services/" in texto
    assert "No service layer exists" not in texto
    assert "Detecting and blocking this is Phase 1 work" not in texto


def test_la_politica_de_seguridad_soporta_la_version_mayor_actual():
    try:
        import tomllib
    except ModuleNotFoundError:                         # Python 3.10
        import tomli as tomllib                         # type: ignore[no-redef]

    version = tomllib.loads(
        (RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    texto = (RAIZ / "SECURITY.md").read_text(encoding="utf-8")
    assert f"`{version.split('.')[0]}.{version.split('.')[1]}.x`" in texto


def test_codex_documenta_el_navegador_oficial_de_plugins():
    for ruta in (RAIZ / "README.md", RAIZ / "docs" / "INSTALL.md"):
        texto = ruta.read_text(encoding="utf-8")
        assert "codex plugin marketplace add" in texto
        assert "`/plugins`" in texto
        assert "codex plugin add horizun-pbi-mcp@horizun" not in texto


#: Cuanto cuenta como "portada" al medir que la instalacion corta va arriba.
#:
#: Era 3000 y `## What it provides` caia en 2902: 98 caracteres de margen, o
#: sea que la guarda prohibia añadir NADA al encabezado, incluida la frase que
#: dice que el producto no necesita licencias -que es de lo que vive su
#: distribucion-. El presupuesto es un sustituto de "va arriba"; lo que fija de
#: verdad el orden son las dos comparaciones de indice de abajo, y esas no se
#: tocan. Se ensancha el sustituto, no la regla.
PORTADA = 4000


def test_el_readme_abre_con_la_ruta_corta_para_codex_y_claude():
    """La instalación filmable debe aparecer antes del bootstrap avanzado."""
    texto = (RAIZ / "README.md").read_text(encoding="utf-8")
    portada = texto[:PORTADA]
    from tests.test_tool_contract import EXPECTED_COUNT

    for frase in ("## Codex", "## Claude Code", "pbi_install_status",
                  "No repository clone", f"{EXPECTED_COUNT} `pbi_*` tools"):
        assert frase in portada, f"la ruta corta perdió {frase!r}"
    assert portada.index("## Codex") < portada.index("## What it provides")
    assert portada.index("## Claude Code") < portada.index("## What it provides")
    assert len(texto.splitlines()) <= 220, (
        "el README volvio a ser un manual interno en vez de una portada")


def test_instalacion_no_promete_locking_como_trabajo_futuro():
    texto = (RAIZ / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "Until Phase 1 adds locking" not in texto
    assert "project locks" in texto
README = RAIZ / "README.md"
AGENTS = RAIZ / "AGENTS.md"


def _sin_bloques_de_codigo(texto: str) -> str:
    return re.sub(r"^```.*?^```", "", texto, flags=re.M | re.S)


# ============================================================================
# DOC-001 — `mode="both"` no puede ofrecerse como si funcionara
# ============================================================================
def test_el_readme_no_instruye_usar_un_modo_bloqueado():
    """El ejemplo es lo peor: no describe, MANDA hacerlo.

    Se buscan solo los usos de `mode`: `scope: report|model|both` y el
    `single|both` de una relación son otra cosa y son correctos.
    """
    texto = README.read_text(encoding="utf-8")
    culpables = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not re.search(r"mode\s*[= ]\s*[\"'`]?both", linea, re.I):
            continue
        # Nombrarlo para decir que está bloqueado es justo lo que hay que
        # hacer; prohibir la palabra obligaría a borrar la explicación junto
        # con el defecto, que es el error de la primera versión de esta prueba.
        if re.search(r"blocked|bloquead|R15|disabled", linea, re.I):
            continue
        culpables.append(f"{n}: {linea.strip()[:90]}")
    assert not culpables, (
        "el README instruye usar `mode both`, que el producto rechaza siempre: "
        + " | ".join(culpables))


def test_donde_se_lista_el_modo_se_dice_que_both_esta_bloqueado():
    """Listar `live|pbip|both` sin más lo presenta como una opción viable."""
    texto = _sin_bloques_de_codigo(README.read_text(encoding="utf-8"))
    sin_aviso = []
    for n, linea in enumerate(texto.splitlines(), 1):
        if not re.search(r"live\s*\|\s*pbip\s*\|\s*both", linea):
            continue
        if not re.search(r"blocked|bloquead|R15|disabled", linea, re.I):
            sin_aviso.append(f"{n}: {linea.strip()[:90]}")
    assert not sin_aviso, (
        "se ofrece `both` como un modo mas, sin decir que esta bloqueado: "
        + " | ".join(sin_aviso))


def test_el_producto_sigue_rechazando_both():
    """Si algun dia se habilita, estas pruebas tienen que caerse, no colar."""
    from horizun_pbi_mcp.services import dual_mode

    with pytest.raises(Exception) as exc:
        dual_mode.assert_mode_is_safely_executable("both")
    assert "both" in str(exc.value).lower()


# ============================================================================
# DOC-002 — la política de publicación tiene que describir lo que se hace
# ============================================================================
def test_agents_no_niega_una_publicacion_que_el_repositorio_hace():
    workflows = RAIZ / ".github" / "workflows"
    publica = [w.name for w in workflows.glob("*.yml")
               if "pypi" in w.read_text(encoding="utf-8").lower()]
    texto = AGENTS.read_text(encoding="utf-8")

    niega = [f"{n}: {l.strip()[:90]}"
             for n, l in enumerate(texto.splitlines(), 1)
             if re.search(r"nothing is published to pypi|no se publica.*pypi",
                          l, re.I)]
    if publica:
        assert not niega, (
            f"AGENTS.md niega una publicacion que {publica} si hace: "
            + " | ".join(niega))


def test_agents_dice_desde_donde_se_publica():
    texto = AGENTS.read_text(encoding="utf-8").lower()
    assert "release.yml" in texto or "por tag" in texto or "on a tag" in texto, (
        "AGENTS.md no describe la politica real de publicacion")


# ============================================================================
# DOC-003 — el «PC vacío» tiene un límite y hay que decirlo
# ============================================================================
def test_el_pc_vacio_declara_que_power_bi_desktop_queda_fuera():
    """Sin Desktop no hay LIVE, ni captura, ni validación de render.

    El instalador trae Python, Node y Claude Code por winget. Prometer un «PC
    completamente vacío» sin decir esto vende medio producto.
    """
    texto = README.read_text(encoding="utf-8")
    assert re.search(
        r"(Power BI Desktop).{0,400}?(not install|no se instala|aparte|"
        r"separately|does not install)",
        texto, re.I | re.S), (
        "el README promete un PC vacio y no dice que Power BI Desktop queda "
        "fuera ni que sin el no hay LIVE, captura ni validacion de render")


# ============================================================================
# DOC-004 — un runbook cuyos comandos no existen es peor que no tenerlo
# ============================================================================
RUNBOOK = RAIZ / "docs" / "RUNBOOK_INSTALACION.md"


def test_existe_el_runbook_de_instalacion():
    assert RUNBOOK.is_file(), (
        "docs/RECOVERY.md solo cubre journals y rollback de escrituras al "
        "proyecto; el ciclo de vida de la instalacion no estaba escrito")


@pytest.mark.parametrize("procedimiento", [
    "Actualizar", "rollback de instalación", "Desinstalar", "Purga",
    "Proxy", "Offline",
])
def test_el_runbook_cubre_cada_procedimiento(procedimiento):
    texto = RUNBOOK.read_text(encoding="utf-8")
    assert procedimiento.lower() in texto.lower(), (
        f"el runbook no cubre «{procedimiento}»")


def test_los_scripts_que_ofrece_el_runbook_existen():
    """La forma mas facil de que un runbook envejezca: renombrar un script."""
    texto = RUNBOOK.read_text(encoding="utf-8")
    guiones = set(re.findall(r"scripts/[A-Za-z0-9_]+\.py", texto))
    assert guiones, "el runbook no ofrece ningun comando ejecutable"
    faltan = sorted(g for g in guiones if not (RAIZ / g).is_file())
    assert not faltan, f"el runbook ofrece scripts que no existen: {faltan}"


#: Lo que el runbook TIENE que declarar ausente, hoy. **Hoy: nada.** La lista se
#: encoge cuando algo deja de faltar, y esa es la senal de que la prueba mide la
#: realidad y no una foto vieja: `uninstall` y `purge` salieron cuando INSTALL-008
#: los implemento, y el bundle offline cuando lo hizo G4.7. Si vuelve a faltar
#: algo, se anade aqui y el runbook tiene que decirlo.
AUSENTES_HOY: tuple[str, ...] = ()

#: Y lo que YA NO puede declarar ausente, porque existe. Cuando la lista de
#: arriba se vacia, esta es la que sostiene la prueba: un runbook que sigue
#: mandando al procedimiento manual de algo que ya tiene comando hace perder el
#: tiempo igual que uno que ofrece un comando inexistente.
YA_NO_FALTAN = ("uninstall", "purge")

#: Lo mismo para lo que no es una opcion de linea de comandos.
COMANDOS_QUE_YA_EXISTEN = ("scripts/bundle.py construir",
                           "scripts/bundle.py instalar")


def test_el_runbook_no_promete_comandos_que_no_existen():
    """Un runbook que ofrece un comando inexistente manda a la gente al vacio.

    Y uno que sigue declarando ausente algo que ya existe, tambien: la persona
    se pone a hacer el procedimiento manual de algo que se resuelve con una
    orden. Se comprueban las dos direcciones.
    """
    texto = RUNBOOK.read_text(encoding="utf-8")
    for ausente in AUSENTES_HOY:
        assert ausente in texto, (
            f"el runbook no declara que falta: {ausente!r}")
    for existe in YA_NO_FALTAN:
        assert f"No existe {existe}" not in texto, (
            f"el runbook declara ausente `{existe}`, que existe desde "
            "INSTALL-008")
        assert f"--{existe}" in texto, (
            f"`--{existe}` existe y el runbook no lo documenta")
    assert "No existe un bundle offline" not in texto, (
        "el runbook declara ausente el bundle offline, que existe desde G4.7")
    for comando in COMANDOS_QUE_YA_EXISTEN:
        assert comando in texto, f"el runbook no documenta `{comando}`"


def test_el_runbook_enumera_antes_de_retirar_nada():
    """El orden importa mas que la prosa: primero se mira, despues se decide.

    Esta prueba buscaba anclas del procedimiento MANUAL de cuatro pasos. Ese
    procedimiento ya no existe -INSTALL-008 lo convirtio en dos comandos- asi
    que ahora se comprueba la propiedad que sobrevive al cambio: que la version
    que NO retira nada aparezca antes que la que si.
    """
    texto = RUNBOOK.read_text(encoding="utf-8")
    assert "outputs" in texto and "backups" in texto, (
        "el runbook no dice cuales son los datos del usuario")

    seco = texto.find("--uninstall\n```")
    confirmado = texto.find("--uninstall --confirm")
    assert 0 < seco < confirmado, (
        "el runbook ofrece la version que borra antes que la que enumera")
    assert "Enumera primero" in texto
