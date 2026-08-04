"""Tres afirmaciones del servidor que eran falsas o llevaban al error.

Salieron de una sesion real construyendo un `.pbip` de punta a punta. Las tres
comparten la misma forma de fallo: el servidor **afirma** algo -en una nota, en
un tema, en una pista- y lo que afirma no se corresponde con la realidad. Nada
de eso rompe una validacion; se paga en el uso.

1. `pbi_refresh_model` decia «Guarda en Power BI Desktop para persistirlo». En
   `.pbip` es falso: ese formato guarda definicion, no datos. El agente repitio
   el consejo tres veces antes de que alguien preguntara que significaba.
2. El preset de tema definia el color y el tamano de las etiquetas de datos
   pero no las encendia, asi que las graficas salian sin numeros.
3. `pbi_page_building_blocks` describia un esquema de spec que el validador
   rechaza. Seguir la pista al pie de la letra daba dos errores seguidos.
"""
from __future__ import annotations

from horizun_pbi_mcp.pbip import page_builder, theme
from horizun_pbi_mcp.powerbi import refresh
from horizun_pbi_mcp.services import page_spec


# =============================================== 1. la nota del refresh =====
class _SesionConProyecto:
    active_pbip = object()


class _SesionSinProyecto:
    active_pbip = None


def test_con_un_pbip_la_nota_no_promete_que_guardar_persista_los_datos():
    nota = refresh._nota_de_persistencia(_SesionConProyecto())
    assert "no los datos" in nota or "solo la DEFINICION" in nota
    assert "refrescar otra vez" in nota
    assert "Guarda en Power BI Desktop para persistirlo" not in nota, (
        "es justo la frase falsa que se estaba propagando al usuario")


def test_sin_proyecto_conocido_no_se_afirma_ningun_formato():
    """Si no se sabe el formato, se explica la diferencia; no se inventa."""
    nota = refresh._nota_de_persistencia(_SesionSinProyecto())
    assert ".pbip" in nota and ".pbix" in nota


def test_la_nota_nunca_revienta_si_la_sesion_falla():
    class _Rota:
        @property
        def active_pbip(self):
            raise RuntimeError("sesion obsoleta")

    nota = refresh._nota_de_persistencia(_Rota())
    assert isinstance(nota, str) and nota


def test_la_respuesta_REAL_del_refresh_trae_la_nota_correcta(monkeypatch):
    """Sobre `refresh_model()`, no sobre la funcion auxiliar.

    Este test existe por un fallo de la primera version: comprobaba
    `_nota_de_persistencia()` por separado, asi que devolver la nota vieja
    codificada a mano en `refresh_model()` seguia pasando en verde. Un test que
    no habria cazado el bug no vale nada, y este se verifico por mutacion
    revirtiendo justo esa linea.
    """
    import contextlib

    class _Tabla:
        Name = "Ventas"

        def RequestRefresh(self, _rt):
            pass

    class _Modelo:
        Tables = [_Tabla()]

        def RequestRefresh(self, _rt):
            pass

        def SaveChanges(self):
            pass

    @contextlib.contextmanager
    def _lease(_sesion):
        yield object()

    @contextlib.contextmanager
    def _connect(_modelo):
        yield (object(), object(), _Modelo())

    monkeypatch.setattr(refresh, "lease_active_model", _lease)
    monkeypatch.setattr(refresh, "connect", _connect)
    monkeypatch.setattr(refresh, "load_tom",
                        lambda: type("T", (), {"RefreshType": type(
                            "R", (), {"Full": object()})})())

    salida = refresh.refresh_model(_SesionConProyecto())
    assert salida["status"] == "ok"
    assert "Guarda en Power BI Desktop para persistirlo" not in salida["note"]
    assert "refrescar otra vez" in salida["note"]


# ============================================ 2. etiquetas de datos =========
def test_los_presets_encienden_las_etiquetas_de_datos():
    """Definir su color sin `show` las deja apagadas: grafica sin numeros."""
    for preset in theme.PRESETS:
        generado = theme.build_theme(preset=preset)
        etiquetas = generado["visualStyles"]["*"]["*"]["labels"][0]
        assert etiquetas.get("show") is True, (
            f"el preset '{preset}' define las etiquetas pero no las enciende: "
            f"{etiquetas}")


# ================================================= 3. la pista del spec =====
def test_el_ejemplo_de_la_pista_es_un_spec_valido():
    """El oraculo es el validador de verdad, no lo que diga el texto."""
    ejemplo = page_builder._example_spec({"width": 1280, "height": 720})
    assert page_spec.validate_schema(ejemplo) == [], (
        "el ejemplo que se le ofrece al agente no pasa validate_schema")


def test_el_ejemplo_respeta_el_lienzo_detectado():
    ejemplo = page_builder._example_spec({"width": 1920, "height": 1080})
    assert ejemplo["page"]["width"] == 1920
    assert ejemplo["page"]["height"] == 1080


def test_el_ejemplo_lleva_las_dos_claves_que_el_hint_omitia():
    ejemplo = page_builder._example_spec({})
    assert "schema_version" in ejemplo
    assert isinstance(ejemplo.get("page"), dict)
