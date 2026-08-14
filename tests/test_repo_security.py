"""RELEASE-003 — los controles que SI viven en el repositorio.

El hallazgo tenia tres mitades y conviene no confundirlas:

1. **Acciones con tags flotantes.** Un tag de action es movil: quien controle
   ese repositorio puede reapuntarlo y ejecutar codigo nuevo en este pipeline
   sin que cambie una linea aqui. Se comprueba leyendo el repositorio, asi que
   se cierra aqui.
2. **Sin CodeQL ni Dependabot.** Archivos de configuracion: tambien se
   comprueban leyendo.
3. **Controles del remoto** -proteccion de rama, revisiones requeridas, secret
   scanning, push protection, security updates, private vulnerability
   reporting-. **No se pueden comprobar desde aqui**, y una prueba que fingiera
   comprobarlos seria peor que no tenerla. Lo que si se puede exigir es que
   esten DECLARADOS como pendientes, que es lo que hace la ultima prueba de
   este archivo.

Esa separacion es el motivo de que RELEASE-003 quede parcial y no cerrada.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml esta en los extras de test")

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"
DEPENDABOT = RAIZ / ".github" / "dependabot.yml"
SECURITY = RAIZ / "SECURITY.md"

#: Los seis ajustes del remoto que RELEASE-003 no puede cerrar desde aqui.
CONTROLES_REMOTOS = (
    "Branch protection",
    "Required reviews",
    "Secret scanning",
    "Push protection",
    "Dependabot",
    "Private vulnerability reporting",
)


# --------------------------------------------------------------- pineado ----
@pytest.mark.parametrize("ruta", sorted(WORKFLOWS.glob("*.yml")),
                         ids=lambda p: p.name)
def test_todas_las_acciones_van_pineadas_por_sha_completo(ruta):
    sueltas = []
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        m = re.search(r"^\s*-?\s*uses:\s*(\S+)", linea)
        if not m:
            continue
        _, _, ref = m.group(1).partition("@")
        if not re.fullmatch(r"[0-9a-f]{40}", ref):
            sueltas.append(f"{n}: {linea.strip()}")
    assert not sueltas, (
        f"{ruta.name} usa referencias moviles:\n" + "\n".join(sueltas))


@pytest.mark.parametrize("ruta", sorted(WORKFLOWS.glob("*.yml")),
                         ids=lambda p: p.name)
def test_cada_sha_lleva_su_version_humana_al_lado(ruta):
    """Un SHA sin comentario es imposible de revisar y de actualizar.

    Nadie sabe de memoria que `3d3c42e5...` es checkout v7, asi que sin el
    comentario la unica forma de revisar un bump es ir a buscarlo, y lo que
    pasa en la practica es que no se revisa.
    """
    sin_comentario = []
    for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"^\s*-?\s*uses:\s*\S+@[0-9a-f]{40}", linea):
            if not re.search(r"#\s*v?\d", linea):
                sin_comentario.append(f"{n}: {linea.strip()}")
    assert not sin_comentario, (
        f"{ruta.name} fija SHA sin decir que version es:\n"
        + "\n".join(sin_comentario))


def test_no_queda_ningun_tag_flotante_en_el_repositorio():
    """La forma directa del hallazgo, sin depender del parseo de YAML."""
    culpables = []
    for ruta in sorted(WORKFLOWS.glob("*.yml")):
        texto = ruta.read_text(encoding="utf-8")
        for movil in re.findall(r"uses:\s*(\S+@(?:v[\d.]+|main|master|latest|release/\S+))",
                                texto):
            culpables.append(f"{ruta.name}: {movil}")
    assert not culpables, f"referencias de action moviles: {culpables}"


# ------------------------------------------------------------- dependabot ---
def test_dependabot_cubre_actions_y_dependencias():
    datos = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
    assert datos["version"] == 2
    ecosistemas = {u["package-ecosystem"] for u in datos["updates"]}
    assert "github-actions" in ecosistemas, (
        "sin esto, pinear por SHA congela tambien los arreglos: un SHA no "
        "caduca ni avisa")
    assert "pip" in ecosistemas, (
        "las dependencias estan acotadas por arriba, asi que un salto mayor no "
        "entra solo; por eso hace falta que alguien avise de que existe")
    for u in datos["updates"]:
        assert u["schedule"]["interval"] in ("daily", "weekly"), (
            f"{u['package-ecosystem']} se revisa con intervalo "
            f"{u['schedule']['interval']}")


# ----------------------------------------------------------------- codeql ---
def test_codeql_existe_y_corre_tambien_por_calendario():
    ruta = WORKFLOWS / "codeql.yml"
    assert ruta.exists(), "no hay analisis estatico de seguridad"
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    disparadores = datos.get(True, datos.get("on"))

    assert "schedule" in disparadores, (
        "sin corrida periodica, una consulta NUEVA de CodeQL no encuentra un "
        "defecto VIEJO hasta que alguien toque ese archivo")
    assert "pull_request" in disparadores

    job = next(iter(datos["jobs"].values()))
    assert job["permissions"].get("security-events") == "write", (
        "CodeQL no podria subir resultados al panel de seguridad")
    assert job["permissions"].get("contents") == "read"
    assert "id-token" not in job["permissions"], (
        "el analisis no publica nada y no necesita OIDC")
    assert "python" in job["strategy"]["matrix"]["language"]


def test_el_permiso_por_defecto_de_todo_workflow_es_minimo():
    for ruta in sorted(WORKFLOWS.glob("*.yml")):
        datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
        permisos = datos.get("permissions")
        assert permisos == {"contents": "read"}, (
            f"{ruta.name} declara permisos por defecto {permisos}; el minimo se "
            "sube job a job, no de entrada")


# --------------------------------------------------------------- SECURITY ---
def test_security_md_dice_las_cinco_cosas_que_tiene_que_decir():
    texto = SECURITY.read_text(encoding="utf-8")
    faltan = [s for s in ("Reporting a vulnerability",   # canal privado
                          "Supported versions",          # versiones soportadas
                          "Scope",                       # alcance
                          "disclosure",                  # proceso de divulgacion
                          "business days")               # tiempos de respuesta
              if s.lower() not in texto.lower()]
    assert not faltan, f"SECURITY.md no cubre: {faltan}"

    assert "do not open a public issue" in texto.lower(), (
        "no se dice lo mas importante: que un issue publico ensena a explotarlo "
        "antes de que nadie pueda arreglarlo")
    assert "Private vulnerability reporting" in texto


def test_security_md_no_afirma_que_los_controles_del_remoto_esten_activos():
    """La mentira comoda seria dar por hechos ajustes que nadie ha comprobado."""
    texto = SECURITY.read_text(encoding="utf-8")
    assert "Pending remote controls" in texto, (
        "SECURITY.md no declara que los controles del remoto siguen pendientes")

    pendientes = texto.split("Pending remote controls", 1)[1]
    faltan = [c for c in CONTROLES_REMOTOS if c.lower() not in pendientes.lower()]
    assert not faltan, f"controles del remoto sin declarar como pendientes: {faltan}"

    # Y cada uno con el comando que lo comprobaria: un pendiente sin forma de
    # verificarlo es un pendiente que nadie cerrara.
    assert pendientes.count("gh api") >= 5, (
        "los controles pendientes no dicen como comprobarlos")
