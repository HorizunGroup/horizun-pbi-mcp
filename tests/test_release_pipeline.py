"""RELEASE-001 y RELEASE-002 — el DAG de publicacion, comprobado.

Dos hallazgos distintos, y el segundo es el que asusta:

* **RELEASE-001.** `ci.yml` probaba en Windows; `publish-pypi.yml` volvia a
  construir en Ubuntu, con otra version de Python y otras versiones de action, y
  publicaba *ese* artefacto. Los bytes aprobados por la suite y los bytes
  subidos a PyPI eran cosas distintas y nadie comparaba sus digests.
* **RELEASE-002.** Los dos workflows de publicacion se disparaban con el mismo
  tag que el CI, sin `needs` que los atara a el. Corrian **en paralelo**: un CI
  en rojo no impedia publicar. Y `workflow_dispatch` sin ningun input publicaba
  con solo pulsar un boton, tambien desde una rama.
* **RELEASE-004**, y este es de omision, que es la clase que ninguna revision
  del YAML ve: el workflow publicaba en PyPI y en el MCP Registry y **no creaba
  ninguna GitHub Release**, mientras el bloque de un pegado del README, de
  `docs/INSTALL.md` y de la skill descargaba el instalador de
  `releases/download/v<version>/...`. El camino de instalacion que se ofrece a
  la gente apuntaba a un asset que ningun job creaba jamas. Nada de lo que se
  comprobaba aqui podia detectarlo: todas las guardas miraban lo que los jobs
  existentes hacian mal, y ninguna preguntaba si faltaba un job.

Las guardas de este archivo estan escritas como funciones que reciben el YAML
ya cargado, y no como aserciones sueltas dentro de cada prueba. Es a proposito:
asi la misma guarda se puede correr sobre el workflow real **y sobre una copia
mutada**. Una guarda que solo se ejecuta contra un archivo correcto no ha
demostrado que detecte nada -es la leccion de CONTRACT-001-, asi que aqui cada
una se rompe adrede y se exige que se encienda.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml esta en los extras de test")

RAIZ = Path(__file__).resolve().parent.parent
WORKFLOWS = RAIZ / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"

#: El job que crea la GitHub Release. Va aparte porque sus permisos son de otra
#: naturaleza: escribe EN ESTE repositorio y no se autentica contra nadie.
JOB_RELEASE = "publicar-github-release"

#: Los jobs que efectivamente publican algo donde alguien lo puede descargar.
JOBS_DE_PUBLICACION = ("publicar-pypi", "publicar-mcp", JOB_RELEASE)

#: Los que salen de GitHub y necesitan un token OIDC para identificarse.
JOBS_OIDC = ("publicar-pypi", "publicar-mcp")

#: Lo que cada publicador tiene que haber esperado, sin excepcion. La release
#: va la ultima de las cuatro: publicarla antes que PyPI es peor que no
#: publicarla, porque la gente descarga el instalador y el `pip install` de
#: dentro no encuentra el paquete.
NEEDS_MINIMOS = {
    "publicar-pypi": ("build", "test"),
    "publicar-mcp": ("build", "test", "publicar-pypi"),
    JOB_RELEASE: ("build", "test", "publicar-pypi", "publicar-mcp"),
}

#: Como se puede crear una release desde un workflow. `release_publish.py` es
#: la nuestra; las otras tres son las formas habituales de hacerlo a mano, y
#: estan aqui para que una de ellas colada en otro job se vea.
CREAN_RELEASE = ("release_publish.py", "gh release create",
                 "softprops/action-gh-release", "actions/create-release")


def cargar(ruta: Path) -> dict:
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


@pytest.fixture
def release() -> dict:
    return cargar(RELEASE)


def _pasos(job: dict) -> list:
    return job.get("steps", []) or []


def _texto_de_pasos(job: dict) -> str:
    trozos = []
    for paso in _pasos(job):
        trozos.append(str(paso.get("run", "")))
        trozos.append(str(paso.get("uses", "")))
        trozos.append(str(paso.get("with", "")))
    return "\n".join(trozos)


# ===================== LAS GUARDAS (se reutilizan en las mutaciones) =========
def _needs(job: dict) -> list:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def guarda_existe_el_job_de_github_release(datos: dict) -> None:
    """RELEASE-004. La guarda que faltaba, y por eso el defecto duro tanto.

    Todas las demas preguntan si los jobs que hay hacen algo mal. Ninguna
    preguntaba si faltaba uno, asi que el workflow que no creaba ninguna
    release pasaba la suite entera en verde mientras el one-paste que se ofrece
    en el README apuntaba a un asset inexistente.
    """
    assert JOB_RELEASE in datos["jobs"], (
        f"no hay job {JOB_RELEASE}: nadie crea la GitHub Release y el bloque "
        "de un pegado descarga de releases/download/, o sea, de un 404")


def guarda_publicacion_depende_de_todo(datos: dict) -> None:
    for nombre in JOBS_DE_PUBLICACION:
        job = datos["jobs"].get(nombre)
        if job is None:            # su ausencia la denuncia la guarda de arriba
            continue
        needs = _needs(job)
        faltan = [r for r in NEEDS_MINIMOS[nombre] if r not in needs]
        assert not faltan, (
            f"{nombre} publica sin esperar a {faltan}: un fallo ahi no lo "
            "detendria")


def guarda_publicacion_solo_desde_un_tag(datos: dict) -> None:
    for nombre in JOBS_DE_PUBLICACION:
        job = datos["jobs"].get(nombre)
        if job is None:
            continue
        condicion = str(job.get("if", ""))
        assert "refs/tags/v" in condicion and "startsWith(github.ref" in condicion, (
            f"{nombre} no exige un tag: se podria publicar desde una rama")


def guarda_dispatch_no_publica_por_defecto(datos: dict) -> None:
    disparadores = datos.get(True, datos.get("on"))       # PyYAML lee `on:` como True
    entradas = (disparadores.get("workflow_dispatch") or {}).get("inputs") or {}
    assert entradas, "workflow_dispatch no pide ningun input: publica con un boton"
    for nombre, campo in entradas.items():
        por_defecto = str(campo.get("default", ""))
        assert por_defecto.lower() in ("no", "false", ""), (
            f"el input {nombre} viene con default {por_defecto!r}")

    for nombre in JOBS_DE_PUBLICACION:
        job = datos["jobs"].get(nombre)
        if job is None:
            continue
        condicion = str(job.get("if", ""))
        assert "github.event.inputs" in condicion, (
            f"{nombre} no mira el input: un dispatch lo publicaria")
        # No vale un 'yes' generico: hay que escribir el tag exacto, asi que un
        # dispatch sobre el tag equivocado no publica.
        assert "github.ref_name" in condicion, (
            f"{nombre} acepta un valor generico en vez del tag exacto")


def guarda_solo_publicacion_pide_id_token(datos: dict) -> None:
    superior = datos.get("permissions") or {}
    assert superior.get("contents") == "read", (
        "el permiso por defecto del workflow no es minimo")
    assert "id-token" not in superior, (
        "id-token: write concedido a TODO el workflow, no solo a quien publica")

    for nombre, job in datos["jobs"].items():
        permisos = job.get("permissions") or {}
        if nombre in JOBS_OIDC:
            assert permisos.get("id-token") == "write", (
                f"{nombre} necesita id-token: write para OIDC")
        else:
            # Incluido el job de la release: escribe AQUI y no se autentica
            # contra nadie de fuera, asi que un token OIDC en sus manos solo
            # amplia lo que un paso comprometido podria hacer.
            assert permisos.get("id-token") is None, (
                f"{nombre} no publica fuera y sin embargo pide id-token")


def guarda_solo_el_job_de_release_escribe_contenidos(datos: dict) -> None:
    """`contents: write` es el permiso para crear tags y releases EN ESTE repo.

    Concederselo a `build` o a `test` significaria que un paso comprometido de
    la construccion puede reescribir el repositorio. Lo tiene uno solo, y es el
    unico que lo necesita.
    """
    escriben = sorted(n for n, job in datos["jobs"].items()
                      if (job.get("permissions") or {}).get("contents") == "write")
    if JOB_RELEASE not in datos["jobs"]:
        assert not escriben, f"escriben contenidos sin crear release: {escriben}"
        return
    assert escriben == [JOB_RELEASE], (
        f"jobs con contents: write = {escriben}; solo puede tenerlo "
        f"{JOB_RELEASE}")


def guarda_publicacion_en_entorno_protegido(datos: dict) -> None:
    for nombre in JOBS_DE_PUBLICACION:
        job = datos["jobs"].get(nombre)
        if job is None:
            continue
        assert job.get("environment"), (
            f"{nombre} publica sin environment: no hay puerta humana posible")


def guarda_nadie_crea_una_release_antes_de_las_pruebas(datos: dict) -> None:
    """Ningun camino llega a publicar sin haber pasado por `build` y `test`.

    No basta con que el job correcto tenga sus `needs`: lo que hay que impedir
    es que APAREZCA un segundo camino —un paso `gh release create` colado en
    `build`, por ejemplo— que publique saltandoselos.
    """
    for nombre, job in datos["jobs"].items():
        texto = _texto_de_pasos(job)
        if not any(marca in texto for marca in CREAN_RELEASE):
            continue
        needs = _needs(job)
        faltan = [r for r in ("build", "test") if r not in needs and r != nombre]
        assert not faltan, (
            f"{nombre} crea una GitHub Release sin esperar a {faltan}: "
            "publicaria bytes que nadie construyo ni probo")


def guarda_una_sola_descarga_del_artifact(datos: dict) -> None:
    """Descargar dos veces es tener dos copias y verificar una."""
    for nombre, job in datos["jobs"].items():
        cuantas = sum(1 for p in _pasos(job)
                      if "download-artifact" in str(p.get("uses", "")))
        assert cuantas <= 1, (
            f"{nombre} descarga el artifact {cuantas} veces; se verifica una y "
            "se usa la otra")


def guarda_la_release_publica_lo_verificado(datos: dict) -> None:
    """El job no improvisa la lista de assets: la deriva de lo firmado."""
    job = datos["jobs"].get(JOB_RELEASE)
    if job is None:
        return
    texto = _texto_de_pasos(job)
    assert "scripts/release_publish.py" in texto, (
        f"{JOB_RELEASE} no usa scripts/release_publish.py: la lista de assets, "
        "la idempotencia y la comprobacion posterior quedarian escritas en YAML, "
        "donde no se pueden probar en local")
    assert "--dir artefactos" in texto, (
        "se publica desde otro sitio que no es el artifact verificado")
    assert "--expect-version" in texto, (
        f"{JOB_RELEASE} no ata el tag a la version que declaran los manifiestos")


def guarda_nadie_reconstruye_al_publicar(datos: dict) -> None:
    for nombre in JOBS_DE_PUBLICACION:
        job = datos["jobs"].get(nombre)
        if job is None:
            continue
        texto = _texto_de_pasos(job)
        for reconstruir in ("python -m build", "pip wheel", "setup.py"):
            assert reconstruir not in texto, (
                f"{nombre} reconstruye con '{reconstruir}': publicaria bytes "
                "que la suite no vio")


def guarda_todo_consumidor_verifica_antes_de_usar(datos: dict) -> None:
    """Descargar el artifact y usarlo sin comprobar el digest no sirve de nada."""
    for nombre, job in datos["jobs"].items():
        pasos = _pasos(job)
        indices_descarga = [i for i, p in enumerate(pasos)
                            if "download-artifact" in str(p.get("uses", ""))]
        if not indices_descarga:
            continue
        indices_verifica = [i for i, p in enumerate(pasos)
                            if "release_verify.py" in str(p.get("run", ""))]
        assert indices_verifica, (
            f"{nombre} descarga el artefacto y nunca lo verifica")
        assert min(indices_verifica) > min(indices_descarga), (
            f"{nombre} verifica antes de descargar: el orden no protege de nada")

        # Y lo que venga DESPUES de la descarga tiene que ir despues de la
        # verificacion: instalar o publicar primero anula la comprobacion.
        primera_verificacion = min(indices_verifica)
        for i, paso in enumerate(pasos):
            if i <= primera_verificacion:
                continue_ = str(paso.get("run", "")) + str(paso.get("uses", ""))
                assert "pypi-publish" not in continue_, (
                    f"{nombre} publica antes de verificar")


def guarda_publicacion_sube_el_directorio_verificado(datos: dict) -> None:
    paso = next((p for p in _pasos(datos["jobs"]["publicar-pypi"])
                 if "pypi-publish" in str(p.get("uses", ""))), None)
    assert paso, "publicar-pypi ya no usa la action oficial de PyPI"
    destino = (paso.get("with") or {}).get("packages-dir")
    assert destino == "artefactos/dist", (
        f"se sube {destino!r} en vez del dist verificado del artefacto")


def guarda_acciones_pineadas_por_sha(datos: dict) -> None:
    sueltas = []
    for nombre, job in datos["jobs"].items():
        for paso in _pasos(job):
            usa = str(paso.get("uses", ""))
            if not usa:
                continue
            _, _, ref = usa.partition("@")
            if not re.fullmatch(r"[0-9a-f]{40}", ref):
                sueltas.append(f"{nombre}: {usa}")
    assert not sueltas, (
        "acciones sin pinear por SHA completo (un tag es movil): " + str(sueltas))


GUARDAS = (
    guarda_existe_el_job_de_github_release,
    guarda_publicacion_depende_de_todo,
    guarda_publicacion_solo_desde_un_tag,
    guarda_dispatch_no_publica_por_defecto,
    guarda_solo_publicacion_pide_id_token,
    guarda_solo_el_job_de_release_escribe_contenidos,
    guarda_publicacion_en_entorno_protegido,
    guarda_nadie_crea_una_release_antes_de_las_pruebas,
    guarda_una_sola_descarga_del_artifact,
    guarda_la_release_publica_lo_verificado,
    guarda_nadie_reconstruye_al_publicar,
    guarda_todo_consumidor_verifica_antes_de_usar,
    guarda_publicacion_sube_el_directorio_verificado,
    guarda_acciones_pineadas_por_sha,
)


# ============================ el workflow real las pasa todas ================
@pytest.mark.parametrize("guarda", GUARDAS, ids=lambda g: g.__name__)
def test_el_workflow_real_pasa_la_guarda(guarda, release):
    guarda(release)


# ============================ y cada guarda se enciende al romperla ==========
def _sin_needs(d):
    d["jobs"]["publicar-pypi"]["needs"] = ["build"]
    return d


def _sin_tag(d):
    """Quita SOLO la clausula del tag y deja intacto el resto de la condicion.

    Sustituir el `if` entero apagaria de paso la guarda del dispatch, y
    entonces la mutacion no probaria que cada guarda esta atada a su defecto:
    probaria que romper dos cosas a la vez enciende dos alarmas.
    """
    cond = d["jobs"]["publicar-pypi"]["if"]
    recortada = cond.replace("startsWith(github.ref, 'refs/tags/v') &&", "").strip()
    assert recortada != cond, "la condicion ya no lleva la clausula del tag"
    d["jobs"]["publicar-pypi"]["if"] = recortada
    return d


def _dispatch_por_defecto(d):
    disparadores = d.get(True, d.get("on"))
    disparadores["workflow_dispatch"]["inputs"]["publicar"]["default"] = "yes"
    return d


def _id_token_global(d):
    d["permissions"]["id-token"] = "write"
    return d


def _sin_environment(d):
    d["jobs"]["publicar-pypi"].pop("environment", None)
    return d


def _reconstruye_al_publicar(d):
    d["jobs"]["publicar-pypi"]["steps"].insert(
        0, {"name": "reconstruir", "run": "python -m build --outdir artefactos/dist"})
    return d


def _publica_sin_verificar(d):
    pasos = d["jobs"]["publicar-pypi"]["steps"]
    d["jobs"]["publicar-pypi"]["steps"] = [
        p for p in pasos if "release_verify.py" not in str(p.get("run", ""))]
    return d


def _sube_otro_directorio(d):
    for p in d["jobs"]["publicar-pypi"]["steps"]:
        if "pypi-publish" in str(p.get("uses", "")):
            p["with"]["packages-dir"] = "dist"
    return d


def _tag_flotante(d):
    d["jobs"]["build"]["steps"][0]["uses"] = "actions/checkout@v7"
    return d


def _sin_job_de_release(d):
    """El defecto tal y como estaba: el workflow entero, sin ese job."""
    d["jobs"].pop(JOB_RELEASE)
    return d


def _release_sin_pypi(d):
    """Publicar la release aunque PyPI falle: el instalador sin paquete detras."""
    d["jobs"][JOB_RELEASE]["needs"] = ["build", "test", "publicar-mcp"]
    return d


def _release_sin_mcp(d):
    d["jobs"][JOB_RELEASE]["needs"] = ["build", "test", "publicar-pypi"]
    return d


def _contents_write_en_build(d):
    d["jobs"]["build"]["permissions"]["contents"] = "write"
    return d


def _release_con_id_token(d):
    d["jobs"][JOB_RELEASE]["permissions"]["id-token"] = "write"
    return d


def _release_a_mano_en_el_build(d):
    """Un segundo camino que publica sin pasar por las pruebas."""
    d["jobs"]["build"]["steps"].append(
        {"name": "atajo", "run": "gh release create v0.0.0 dist/*"})
    return d


def _release_descarga_dos_veces(d):
    pasos = d["jobs"][JOB_RELEASE]["steps"]
    descarga = next(p for p in pasos if "download-artifact" in str(p.get("uses", "")))
    pasos.append(dict(descarga))
    return d


def _release_improvisa_los_assets(d):
    """Subir «lo que haya» en vez de lo firmado en SHA256SUMS."""
    pasos = d["jobs"][JOB_RELEASE]["steps"]
    for p in pasos:
        if "release_publish.py" in str(p.get("run", "")):
            p["run"] = "gh release create ${{ github.ref_name }} artefactos/dist/*"
    return d


MUTACIONES = [
    ("borrar el job de la GitHub Release", _sin_job_de_release,
     guarda_existe_el_job_de_github_release),
    ("publicar la release sin esperar a PyPI", _release_sin_pypi,
     guarda_publicacion_depende_de_todo),
    ("publicar la release sin esperar al MCP Registry", _release_sin_mcp,
     guarda_publicacion_depende_de_todo),
    ("dar contents: write al job de construccion", _contents_write_en_build,
     guarda_solo_el_job_de_release_escribe_contenidos),
    ("pedir id-token en el job de la release", _release_con_id_token,
     guarda_solo_publicacion_pide_id_token),
    ("crear una release a mano dentro de build", _release_a_mano_en_el_build,
     guarda_nadie_crea_una_release_antes_de_las_pruebas),
    ("descargar el artifact dos veces", _release_descarga_dos_veces,
     guarda_una_sola_descarga_del_artifact),
    ("subir un glob en vez de lo firmado", _release_improvisa_los_assets,
     guarda_la_release_publica_lo_verificado),
    ("quitar `needs: test` de publicar-pypi", _sin_needs,
     guarda_publicacion_depende_de_todo),
    ("permitir publicar desde una rama", _sin_tag,
     guarda_publicacion_solo_desde_un_tag),
    ("dejar el input de dispatch en 'yes'", _dispatch_por_defecto,
     guarda_dispatch_no_publica_por_defecto),
    ("conceder id-token a todo el workflow", _id_token_global,
     guarda_solo_publicacion_pide_id_token),
    ("publicar sin environment protegido", _sin_environment,
     guarda_publicacion_en_entorno_protegido),
    ("reconstruir en el job de publicacion", _reconstruye_al_publicar,
     guarda_nadie_reconstruye_al_publicar),
    ("publicar sin verificar el digest", _publica_sin_verificar,
     guarda_todo_consumidor_verifica_antes_de_usar),
    ("subir un directorio sin verificar", _sube_otro_directorio,
     guarda_publicacion_sube_el_directorio_verificado),
    ("volver a un tag flotante de action", _tag_flotante,
     guarda_acciones_pineadas_por_sha),
]


@pytest.mark.parametrize("descripcion,mutar,guarda", MUTACIONES,
                         ids=[m[0] for m in MUTACIONES])
def test_la_guarda_se_enciende_cuando_se_rompe_el_workflow(
        descripcion, mutar, guarda, release):
    """Una mutacion que sobrevive es una guarda que no ata nada."""
    roto = mutar(copy.deepcopy(release))
    with pytest.raises(AssertionError):
        guarda(roto)


def test_ninguna_mutacion_apaga_de_mas(release):
    """Cada guarda esta atada a SU defecto, no al ruido de al lado.

    Sin esto, una guarda demasiado amplia -que fallara con cualquier cambio-
    pareceria fuerte y en realidad solo seria ruidosa.
    """
    for descripcion, mutar, propia in MUTACIONES:
        roto = mutar(copy.deepcopy(release))
        for otra in GUARDAS:
            if otra is propia:
                continue
            try:
                otra(roto)
            except AssertionError as exc:                   # pragma: no cover
                pytest.fail(f"la mutacion «{descripcion}» tambien apago "
                            f"{otra.__name__}: {exc}")


# ============================ el resto del DAG ==============================
def test_el_ci_ya_no_se_dispara_con_un_tag(release):
    """No puede haber dos pipelines corriendo sobre el mismo tag."""
    ci = cargar(WORKFLOWS / "ci.yml")
    disparadores = ci.get(True, ci.get("on"))
    assert "tags" not in (disparadores.get("push") or {}), (
        "ci.yml vuelve a dispararse con tags: eso es un pipeline en paralelo "
        "al de release, que es el defecto de RELEASE-002")


def test_no_quedan_workflows_que_publiquen_por_su_cuenta():
    """`publish-pypi.yml` y `publish-mcp.yml` publicaban sin depender del CI."""
    huerfanos = []
    for w in sorted(WORKFLOWS.glob("*.yml")):
        if w.name == "release.yml":
            continue
        texto = w.read_text(encoding="utf-8")
        if "pypi-publish" in texto or "mcp-publisher publish" in texto:
            huerfanos.append(w.name)
    assert not huerfanos, (
        f"{huerfanos} publican fuera del DAG de release.yml")


def test_el_ci_tambien_esta_pineado_por_sha():
    guarda_acciones_pineadas_por_sha(cargar(WORKFLOWS / "ci.yml"))


def test_ningun_workflow_usa_una_referencia_movil():
    culpables = []
    for w in sorted(WORKFLOWS.glob("*.yml")):
        texto = w.read_text(encoding="utf-8")
        for movil in ("@main", "@master", "@release/v1", "releases/latest"):
            if movil in texto:
                culpables.append(f"{w.name}: {movil}")
    assert not culpables, f"referencias moviles en workflows: {culpables}"


def test_el_build_es_uno_solo_y_los_demas_lo_consumen(release):
    """El corazon de RELEASE-001, dicho como estructura y no como intencion."""
    construyen = [n for n, job in release["jobs"].items()
                  if "release_build.py" in _texto_de_pasos(job)]
    assert construyen == ["build"], (
        f"construyen el artefacto {construyen}; solo puede hacerlo `build`")

    suben = [n for n, job in release["jobs"].items()
             if "upload-artifact" in _texto_de_pasos(job)]
    assert suben == ["build"], f"suben artefacto {suben}"

    consumidores = [n for n, job in release["jobs"].items()
                    if "download-artifact" in _texto_de_pasos(job)]
    assert set(consumidores) == {"test", *JOBS_DE_PUBLICACION}, (
        f"consumidores inesperados del artefacto: {consumidores}")


def test_el_job_de_pruebas_corre_contra_el_paquete_instalado(release):
    """Si `src/` tapa al wheel, el job prueba el checkout otra vez.

    `pythonpath = ["src"]` vive en pyproject, asi que hay que anularlo
    explicitamente o la instalacion limpia no demuestra nada.
    """
    texto = _texto_de_pasos(release["jobs"]["test"])
    assert "pip install $wheel" in texto, "el job no instala el wheel construido"
    assert "-o pythonpath=" in texto, (
        "pytest correria con src/ en el path y taparia el paquete instalado")
    assert "pip install -e" not in texto, "el job instala el checkout en editable"


def test_el_job_de_pruebas_cubre_las_dos_versiones_de_python(release):
    matriz = release["jobs"]["test"]["strategy"]["matrix"]["python-version"]
    assert "3.10" in matriz and "3.13" in matriz, (
        f"la matriz de release no cubre el rango declarado: {matriz}")
    assert release["jobs"]["test"]["runs-on"] == "windows-latest"


def test_los_scripts_del_pipeline_existen_y_son_los_que_se_llaman(release):
    for script in ("scripts/release_build.py", "scripts/release_verify.py"):
        assert (RAIZ / script).exists(), f"falta {script}"
    texto = "\n".join(_texto_de_pasos(j) for j in release["jobs"].values())
    assert "scripts/release_build.py" in texto
    assert "scripts/release_verify.py" in texto
