"""Fase D — atomicidad de los workflows multiarchivo, con fault injection.

Dos defectos que el inventario historico de R13 no cubria, porque su chequeo
era lexico y la transaccion se abre dentro de la funcion LLAMADA, no dentro del
bucle:

- `repair_broken_references` iteraba llamando a `replace_visual_field`, una
  transaccion por visual, **capturando la excepcion para continuar**. Si
  fallaba el quinto, los cuatro anteriores quedaban confirmados y la tool
  devolvia `ok:true` con una lista de fallidos.
- `normalize_report` llamaba a `update_visuals_bulk` una vez por pagina:
  atomico dentro de cada pagina, pero si fallaba la tercera, las dos primeras
  quedaban reacomodadas y el informe a medio normalizar.

Las pruebas inyectan el fallo en cada frontera y exigen restauracion byte a
byte y cero directorios huerfanos.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pbip import pbir_reader, pbir_writer, project_locator, tmdl_reader
from services import pbir_edit, workflows
from services import txn as txn_service
from tests.fixtures import synthetic


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


def directorios(project: Path) -> set:
    return {str(p.relative_to(project)).replace("\\", "/")
            for p in project.rglob("*") if p.is_dir()}


def desordenar(active, paginas_extra: int = 2) -> int:
    """Deja el informe con VARIAS paginas y el layout roto.

    El fixture sintetico trae una sola pagina ya normalizada, asi que
    `normalize_report` no tendria nada que hacer y la inyeccion de fallos no
    llegaria a dispararse. La atomicidad que interesa es justo la de VARIAS
    paginas: es ahi donde antes se abria una transaccion por pagina.

    Se escribe directamente sobre los visual.json porque esto es preparacion
    del escenario, no la operacion bajo prueba.
    """
    import json

    origen = pbir_reader.list_pages(active)[0]["display_name"]
    for i in range(paginas_extra):
        pbir_edit.duplicate_page(active, origen, f"Desordenada {i + 1}")

    tocados = 0
    for p in pbir_reader.list_pages(active):
        for j, v in enumerate(pbir_reader.list_visuals(active, p["display_name"])):
            ruta = Path(v["file"])
            datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
            # Solapados y fuera del lienzo: el layout doctor lo marcara.
            datos["position"] = {"x": 5 + j, "y": 5 + j, "width": 1400,
                                 "height": 900, "z": 0}
            ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False),
                            encoding="utf-8", newline="\r\n")
            tocados += 1
    return tocados


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, tmdl_reader.read_semantic_model(active), pbip.parent


@pytest.fixture
def desordenado(proyecto):
    """Proyecto con 3 paginas y layout roto: normalize tiene trabajo real."""
    active, md, raiz = proyecto
    n = desordenar(active, paginas_extra=2)
    assert n >= 3, "hacen falta al menos 3 visuales para inyectar en 3 fronteras"
    return active, md, raiz


def visuales_de(active):
    salida = []
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["display_name"]):
            salida.append((p["display_name"], v))
    return salida


class FalloInyectado(RuntimeError):
    """Marca para distinguir el fallo de la prueba de uno real."""


def inyectar(monkeypatch, en_la_escritura: int):
    """Hace fallar la enesima escritura JSON de una transaccion (1 = primera)."""
    original = txn_service.Transaction.write_json
    estado = {"n": 0}

    def envuelto(self, target, data):
        estado["n"] += 1
        if estado["n"] == en_la_escritura:
            raise FalloInyectado(f"fallo inyectado en la escritura {en_la_escritura}")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", envuelto)
    return estado


# ============================================ una sola transaccion, medida ====
def test_normalize_report_abre_una_sola_transaccion(desordenado, monkeypatch,
                                                    isolated_settings):
    """Antes: una por pagina. El journal lo delata."""
    active, md, raiz = desordenado
    abiertas = []
    original = txn_service.project_transaction

    def contar(*a, **k):
        abiertas.append(k.get("tool") or (a[2] if len(a) > 2 else "?"))
        return original(*a, **k)

    monkeypatch.setattr(txn_service, "project_transaction", contar)
    workflows.normalize_report(active, md, dry_run=False)

    assert len(abiertas) <= 1, (
        f"normalize_report abrio {len(abiertas)} transacciones: {abiertas}")


def test_repair_abre_una_sola_transaccion(proyecto, monkeypatch):
    active, md, raiz = proyecto
    pares = visuales_de(active)
    if not pares:
        pytest.skip("el fixture no tiene visuales")

    abiertas = []
    original = txn_service.project_transaction
    monkeypatch.setattr(txn_service, "project_transaction",
                        lambda *a, **k: (abiertas.append(1), original(*a, **k))[1])

    medidas = [m["name"] for m in md.get("measures") or []]
    if len(medidas) < 2:
        pytest.skip("hacen falta dos medidas")

    workflows.repair_broken_references(
        active, md, mapping={"[NoExiste]": f"[{medidas[0]}]"}, dry_run=True)
    assert len(abiertas) == 0, "un dry_run no puede abrir transacciones"


# ==================================== fault injection en cada frontera ========
@pytest.mark.parametrize("frontera,escritura", [
    ("primera", 1),
    ("intermedia", 2),
    ("ultima", 3),
])
def test_fallo_en_cada_escritura_restaura_byte_a_byte(desordenado, monkeypatch,
                                                      frontera, escritura,
                                                      isolated_settings):
    active, md, raiz = desordenado
    antes_h, antes_d = huella(raiz), directorios(raiz)

    inyectar(monkeypatch, escritura)
    with pytest.raises(Exception):
        workflows.normalize_report(active, md, dry_run=False)

    assert huella(raiz) == antes_h, (
        f"fallo en la escritura {frontera}: el proyecto no quedo byte a byte igual")
    assert directorios(raiz) == antes_d, "quedaron directorios huerfanos"


def test_fallo_en_la_validacion_previa_no_escribe_nada(proyecto, monkeypatch):
    """Si un visual del lote no existe, no se escribe NINGUNO."""
    active, md, raiz = proyecto
    antes = huella(raiz)

    pares = visuales_de(active)
    if not pares:
        pytest.skip("el fixture no tiene visuales")
    pagina = pares[0][0]

    updates = [{"visual_id": v["id"], "x": 10, "y": 10, "width": 100, "height": 100}
               for _p, v in pares if _p == pagina]
    updates.append({"visual_id": "no_existe_este_visual", "x": 0, "y": 0,
                    "width": 10, "height": 10})

    with pytest.raises(Exception):
        pbir_writer.update_visuals_bulk(active, pagina, updates,
                                        tool="prueba_validacion")
    assert huella(raiz) == antes, (
        "un objetivo invalido debe abortar el lote antes de escribir")


def test_fallo_en_el_commit_restaura(desordenado, monkeypatch, isolated_settings):
    """El fallo llega al cerrar la transaccion, con todo ya escrito."""
    active, md, raiz = desordenado
    antes = huella(raiz)

    original = txn_service.Transaction.commit

    def commit_roto(self, *a, **k):
        raise FalloInyectado("fallo inyectado en el commit")

    monkeypatch.setattr(txn_service.Transaction, "commit", commit_roto)
    with pytest.raises(Exception):
        workflows.normalize_report(active, md, dry_run=False)

    assert huella(raiz) == antes, "un fallo en el commit debe revertir todo"


def test_fallo_en_la_restauracion_se_reporta_no_se_oculta(desordenado, monkeypatch,
                                                          isolated_settings):
    """Si la compensacion no puede completarse, NO se dice que todo fue bien."""
    active, md, raiz = desordenado

    inyectar(monkeypatch, 2)
    original = txn_service.Transaction._undo               # noqa: SLF001
    visto = {"n": 0}

    def undo_sucio(self, status, cause=None):
        """Simula una restauracion que no pudo completarse."""
        visto["n"] += 1
        salida = original(self, status, cause)
        salida["clean"] = False
        salida.setdefault("by_outcome", {})["rollback_failed"] = ["simulado"]
        return salida

    monkeypatch.setattr(txn_service.Transaction, "_undo", undo_sucio)

    with pytest.raises(txn_service.RollbackIncompleteError) as exc:
        workflows.normalize_report(active, md, dry_run=False)

    assert visto["n"] > 0, "la prueba no llego a ejercitar la restauracion"
    assert "journal" in str(exc.value).lower(), (
        "un rollback incompleto debe decir donde estan los originales")


# ================================= repair: un fallo aborta el lote entero =====
def romper_referencias(active, cuantas: int = 3) -> str:
    """Hace que varios visuales apunten a una medida inexistente.

    `repair_broken_references` solo actua sobre referencias ROTAS, asi que hay
    que romperlas de verdad para llegar a la fase de escritura.
    """
    import json

    roto = "MedidaQueNoExiste"
    tocados = 0
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["display_name"]):
            if tocados >= cuantas:
                break
            ruta = Path(v["file"])
            datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
            query = datos.get("visual", {}).get("query", {}).get("queryState", {})
            cambiado = False
            for spec in query.values():
                for proy in spec.get("projections", []):
                    # Solo nodos Measure: mapear un nodo Column a una medida lo
                    # rechazaria la validacion de tipo (E1/H6), y aqui se quiere
                    # ejercitar la atomicidad, no esa validacion.
                    if "Measure" in proy.get("field", {}):
                        proy["field"]["Measure"]["Property"] = roto
                        cambiado = True
            if cambiado:
                ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False),
                                encoding="utf-8", newline="\r\n")
                tocados += 1
    return roto


def test_repair_no_deja_reparaciones_a_medias(proyecto, monkeypatch,
                                              isolated_settings):
    """REGRESION: antes el `except` dejaba confirmadas las anteriores."""
    active, md, raiz = proyecto
    desordenar(active, paginas_extra=2)
    roto = romper_referencias(active, cuantas=3)

    medidas = [m["name"] for m in md.get("measures") or []]
    if not medidas:
        pytest.skip("el modelo sintetico no tiene medidas")

    # El formato exacto de la referencia rota lo decide el lector; se toma del
    # propio diagnostico en vez de suponerlo.
    diagnostico = workflows.repair_broken_references(active, md, dry_run=True)
    referencias = set()
    for etapa in diagnostico.get("stages", []):
        for r in (etapa.get("result") or {}).get("broken", []):
            referencias.add(r["reference"])

    assert len(referencias) >= 1, (
        f"no se detectaron las referencias rotas recien creadas: {diagnostico}")
    mapping = {ref: f"[{medidas[0]}]" for ref in referencias}
    antes = huella(raiz)
    inyectar(monkeypatch, 2)          # falla la SEGUNDA de las escrituras

    with pytest.raises(Exception):
        workflows.repair_broken_references(active, md, mapping=mapping,
                                           dry_run=False)

    assert huella(raiz) == antes, (
        "la primera reparacion quedo confirmada pese a fallar la segunda")


def test_repair_ya_no_devuelve_ok_con_suboperaciones_fallidas():
    """El resultado no puede traer una lista de fallidos y decir applied=True."""
    import inspect

    fuente = inspect.getsource(workflows.repair_broken_references)
    assert "fallidas.append" not in fuente, (
        "vuelve a acumular fallos para continuar en vez de abortar el lote")
    assert "except Exception" not in fuente, (
        "vuelve a capturar la excepcion dentro del bucle de escritura")


# ============================================ el inventario, como prueba ======
def test_ninguna_transaccion_dentro_de_un_bucle():
    """Chequeo lexico. Complementa, no sustituye, a las pruebas de arriba:
    no ve una transaccion abierta dentro de la funcion llamada."""
    import ast
    import pathlib

    fallos = []
    for archivo in pathlib.Path("src").rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        try:
            arbol = ast.parse(texto)
        except SyntaxError:
            continue
        for bucle in [n for n in ast.walk(arbol) if isinstance(n, (ast.For, ast.While))]:
            src = ast.get_source_segment(texto, bucle) or ""
            if "project_transaction(" in src:
                fallos.append(f"{archivo}:{bucle.lineno}")
    assert not fallos, f"transaccion abierta dentro de un bucle: {fallos}"


def test_los_workflows_no_llaman_a_escritores_transaccionales_en_bucle():
    """Lo que el chequeo lexico NO ve: el bucle llama y la funcion abre.

    Es el patron exacto que tenian repair_broken_references y normalize_report.
    """
    import ast
    import pathlib

    # Funciones que abren su propia transaccion: llamarlas en bucle es el bug.
    transaccionales = set()
    for archivo in pathlib.Path("src").rglob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        try:
            arbol = ast.parse(texto)
        except SyntaxError:
            continue
        for fn in [n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef)]:
            if "project_transaction(" in (ast.get_source_segment(texto, fn) or ""):
                transaccionales.add(fn.name)

    fuente = pathlib.Path("src/services/workflows.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    fallos = []
    for bucle in [n for n in ast.walk(arbol) if isinstance(n, (ast.For, ast.While))]:
        for llamada in [n for n in ast.walk(bucle) if isinstance(n, ast.Call)]:
            nombre = getattr(llamada.func, "attr", None) or getattr(llamada.func, "id", None)
            if nombre in transaccionales:
                fallos.append(f"workflows.py:{llamada.lineno} llama a {nombre}()")

    assert not fallos, (
        "un workflow llama en bucle a una funcion que abre su propia "
        f"transaccion: {fallos}")
