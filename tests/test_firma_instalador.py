"""FIRMA — el instalador va firmado EN EL ARBOL y sus pines son los del archivo firmado.

El unico binario con firma de este repo es `scripts/instalar.ps1`. Su SHA-256
esta FIJADO en `downloads_manifest.json`, en el bloque de un pegado
(`scripts/one_paste.ps1`, que lo verifica antes de ejecutar nada) y en las
copias de ese bloque (`docs/INSTALL.md`, la skill). Firmarlo DESPUES de
construir la release cambia su hash y deja el one-paste rechazando el
instalador publicado: la v2.1.2 se re-firmo a posteriori, el asset publicado
paso de 31600 a 45966 bytes y el bloque siguio apuntando al hash de antes.

El diseno que estas pruebas fijan:

1. se firma `scripts/instalar.ps1` en el arbol (Authenticode, bloque
   `# SIG # Begin signature block` al final), ANTES de etiquetar;
2. `scripts/pin_instalador.py` recalcula los pines sobre el archivo FIRMADO;
3. el pipeline (`release_build`, `release_verify`, `release_publish`) EXIGE la
   firma y el hash del archivo firmado: sin firma, para con un mensaje que dice
   por que.

Las pruebas de forma usan bloques de firma sinteticos: la criptografia la
comprueba Windows (`Get-AuthenticodeSignature`), no estas pruebas.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import pin_instalador as pin                                     # noqa: E402
import release_build as rb                                       # noqa: E402
import release_verify as rv                                      # noqa: E402
import sync_one_paste as sop                                     # noqa: E402

INSTALADOR = RAIZ / "scripts" / "instalar.ps1"

CUERPO = b"param()\nWrite-Host 'hola'\n"
BLOQUE = (b"# SIG # Begin signature block\r\n# MIIabcdef\r\n# 1234567890\r\n"
          b"# SIG # End signature block\r\n")


# ============================================ el instalador REAL del arbol ===
def test_el_instalador_del_arbol_esta_firmado():
    """Falla de forma EXPLICITA mientras el instalador no este firmado.

    Es el semaforo del PR de release: en rojo hasta que se firma con la clave
    Certum y se recalculan los pines (RELEASING.md). El mensaje dice que falta
    y donde esta el procedimiento.
    """
    crudo = INSTALADOR.read_bytes()
    info = rv.exigir_firmado(crudo, "scripts/instalar.ps1")
    assert info["firmado"] is True


def test_los_pines_son_los_del_archivo_firmado():
    """manifest, bloque de un pegado, INSTALL.md y la skill: todos al archivo firmado."""
    res = subprocess.run(
        [sys.executable, str(RAIZ / "scripts" / "pin_instalador.py"), "--check"],
        capture_output=True, text=True, timeout=120,
        encoding="utf-8", errors="replace")
    assert res.returncode == 0, (
        "los pines no son los del instalador firmado; ejecuta "
        f"python scripts/pin_instalador.py\n{res.stdout}\n{res.stderr}")


@pytest.mark.skipif(sys.platform != "win32",
                    reason="Get-AuthenticodeSignature es de Windows")
def test_windows_no_ve_el_instalador_alterado():
    """Detecta lo que ningun hash del repo ve: la firma ya no cubre el cuerpo.

    Que git convierta finales de linea, o que alguien edite el .ps1 despues de
    firmarlo, deja la firma intacta pero con `HashMismatch`. No se exige
    `Valid` (eso depende de la raiz de confianza del equipo que corre la
    prueba; lo exige `release_verify --authenticode` en el build), sino que el
    estado NO sea ninguno de los que delatan un archivo roto o sin firmar.
    """
    ps = shutil.which("pwsh") or shutil.which("powershell")
    assert ps, "no hay PowerShell"
    res = subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-Command",
         "(Get-AuthenticodeSignature -LiteralPath $env:HZ_RUTA).Status.ToString()"],
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, HZ_RUTA=str(INSTALADOR)))
    estado = res.stdout.strip().splitlines()[-1]
    assert estado not in ("NotSigned", "HashMismatch"), (
        f"Get-AuthenticodeSignature dice {estado} para scripts/instalar.ps1")


def test_git_no_toca_los_bytes_firmados():
    """`* text=auto eol=lf` reescribiria el bloque de firma: el .ps1 va sin normalizar."""
    git = shutil.which("git")
    if not git:
        pytest.skip("git no esta disponible")
    res = subprocess.run([git, "check-attr", "text", "eol", "--",
                          "scripts/instalar.ps1"], cwd=RAIZ,
                         capture_output=True, text=True, timeout=60)
    if res.returncode != 0:
        pytest.skip("no es un repositorio git")
    assert "text: unset" in res.stdout, (
        "scripts/instalar.ps1 debe ir con '-text' en .gitattributes: git no puede "
        f"normalizar los bytes que cubre el hash fijado.\n{res.stdout}")


def test_el_manifest_declara_el_hash_del_archivo_firmado():
    crudo = INSTALADOR.read_bytes()
    entrada = json.loads((RAIZ / "scripts" / "downloads_manifest.json")
                         .read_text(encoding="utf-8"))["downloads"]["instalar.ps1"]
    assert entrada["signed"] is True
    assert hashlib.sha256(crudo).hexdigest() == entrada["sha256"]
    assert len(crudo) == entrada["actual_bytes"]


# ============================================ la forma (bytes sinteticos) ====
def test_un_instalador_sin_firmar_es_valido_en_forma_pero_no_esta_firmado():
    info = rv.analizar_instalador(CUERPO)
    assert info["firmado"] is False and info["cuerpo"] == CUERPO


def test_un_instalador_sin_firmar_se_rechaza_con_un_mensaje_que_dice_por_que():
    with pytest.raises(SystemExit) as exc:
        rv.exigir_firmado(CUERPO, "scripts/instalar.ps1")
    mensaje = str(exc.value)
    assert "NO ESTA FIRMADO" in mensaje
    assert "RELEASING.md" in mensaje, "no apunta al procedimiento"


def test_el_bloque_de_firma_con_crlf_se_acepta():
    info = rv.analizar_instalador(CUERPO + BLOQUE)
    assert info["firmado"] is True
    assert info["cuerpo"] == CUERPO
    assert info["bloque"] == BLOQUE


def test_el_crlf_que_signtool_intercala_antes_del_bloque_se_acepta():
    """signtool deja un CRLF entre el cuerpo y `# SIG # Begin`: ya es lo firmado."""
    info = rv.analizar_instalador(CUERPO + b"\r\n" + BLOQUE)
    assert info["firmado"] is True
    assert info["cuerpo"] == CUERPO + b"\r\n"


def test_dos_crlf_antes_del_bloque_no_se_aceptan():
    with pytest.raises(rv.InstaladorInvalido, match="CUERPO"):
        rv.analizar_instalador(CUERPO + b"\r\n\r\n" + BLOQUE)


def test_el_bloque_de_firma_con_lf_se_acepta():
    info = rv.analizar_instalador(CUERPO + BLOQUE.replace(b"\r\n", b"\n"))
    assert info["firmado"] is True


@pytest.mark.parametrize("crudo,fragmento", [
    (b"\xef\xbb\xbf" + CUERPO + BLOQUE, "BOM"),
    (CUERPO.replace(b"\n", b"\r\n") + BLOQUE, "CUERPO"),
    (CUERPO + b"# acentu\xc3\xa1do\n" + BLOQUE, "ASCII"),
    (CUERPO + BLOQUE + b"Write-Host 'colado despues'\n", "End signature block"),
    (CUERPO + BLOQUE + BLOQUE, "mas de un bloque"),
    (CUERPO + BLOQUE.replace(b"# MIIabcdef", b"MIIabcdef"), "prefijo"),
    (CUERPO + BLOQUE.replace(b"# 1234567890", b"# 12345\xc3\xa167"), "ASCII"),
    (b"Write-Host 'x'  # SIG # Begin signature block\n", "no abre una linea"),
])
def test_formas_invalidas_se_rechazan(crudo, fragmento):
    with pytest.raises(rv.InstaladorInvalido, match=fragmento):
        rv.analizar_instalador(crudo)


def test_un_cr_en_el_cuerpo_no_se_cuela_por_tener_firma():
    """Solo el bloque admite CRLF: el cuerpo con CR cambiaria el hash al descargar."""
    with pytest.raises(SystemExit, match="CUERPO"):
        rv.exigir_firmado(b"param()\r\nWrite-Host 1\n" + BLOQUE)


# ============================== release_build para SIN construir sin firma ====
def test_release_build_para_antes_de_construir_si_el_instalador_no_esta_firmado(
        tmp_path, monkeypatch):
    sin_firma = tmp_path / "instalar.ps1"
    sin_firma.write_bytes(CUERPO)
    monkeypatch.setattr(rb, "INSTALADOR", sin_firma)

    with pytest.raises(SystemExit, match="NO ESTA FIRMADO"):
        rb.construir(tmp_path / "salida")
    # Y para ANTES de gastar minutos: no se llego a crear ningun venv.
    assert not (tmp_path / "salida" / ".trabajo" / "venv_build").exists()


# ================================================ pin_instalador (aislado) ===
@pytest.fixture
def arbol(tmp_path, monkeypatch):
    """Una copia minima del arbol con un instalador sintetico."""
    for rel in ("scripts/downloads_manifest.json", "scripts/one_paste.ps1",
                "docs/INSTALL.md", "skills/horizun-pbi-setup/SKILL.md"):
        destino = tmp_path / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes((RAIZ / rel).read_bytes())
    instalador = tmp_path / "scripts" / "instalar.ps1"
    monkeypatch.setattr(pin, "RAIZ", tmp_path)
    monkeypatch.setattr(pin, "INSTALADOR", instalador)
    monkeypatch.setattr(pin, "MANIFEST", tmp_path / "scripts" / "downloads_manifest.json")
    monkeypatch.setattr(pin, "ONE_PASTE", tmp_path / "scripts" / "one_paste.ps1")
    return tmp_path


def test_pinear_se_niega_a_fijar_el_hash_de_un_instalador_sin_firmar(arbol):
    (arbol / "scripts" / "instalar.ps1").write_bytes(CUERPO)
    with pytest.raises(SystemExit, match="NO ESTA FIRMADO"):
        pin.pinear(solo_comprobar=False)


def test_pinear_deja_manifest_bloque_y_docs_en_el_hash_del_archivo_firmado(arbol):
    firmado = CUERPO + BLOQUE
    (arbol / "scripts" / "instalar.ps1").write_bytes(firmado)
    esperado = hashlib.sha256(firmado).hexdigest()

    assert pin.pinear(solo_comprobar=True) == 1, "debia detectar el desfase"
    assert pin.pinear(solo_comprobar=False) == 0

    entrada = json.loads((arbol / "scripts" / "downloads_manifest.json")
                         .read_text(encoding="utf-8"))["downloads"]["instalar.ps1"]
    assert entrada["sha256"] == esperado
    assert entrada["actual_bytes"] == len(firmado)
    for rel in ("scripts/one_paste.ps1", "docs/INSTALL.md",
                "skills/horizun-pbi-setup/SKILL.md"):
        assert esperado in (arbol / rel).read_text(encoding="utf-8"), rel
    assert pin.pinear(solo_comprobar=True) == 0, "tras pinear no debe haber desfase"


def test_pinear_detecta_que_se_toco_el_instalador_despues_de_pinear(arbol):
    ruta = arbol / "scripts" / "instalar.ps1"
    ruta.write_bytes(CUERPO + BLOQUE)
    assert pin.pinear(solo_comprobar=False) == 0
    # Se edita el cuerpo y se vuelve a firmar (otro bloque): los pines caducan.
    ruta.write_bytes(CUERPO + b"Write-Host 'otro'\n" + BLOQUE.replace(b"MIIabc", b"MIIxyz"))
    assert pin.pinear(solo_comprobar=True) == 1


def test_el_bloque_de_las_copias_sigue_sincronizado_tras_pinear(arbol):
    (arbol / "scripts" / "instalar.ps1").write_bytes(CUERPO + BLOQUE)
    pin.pinear(solo_comprobar=False)
    canonico = "\n".join(
        (arbol / "scripts" / "one_paste.ps1").read_text(encoding="ascii").splitlines()[
            next(n for n, l in enumerate(
                (arbol / "scripts" / "one_paste.ps1").read_text(encoding="ascii").splitlines())
                if l.strip() and not l.startswith("#")):]).rstrip("\n")
    for rel in sop.DOCUMENTOS:
        texto = (arbol / rel).read_text(encoding="utf-8")
        _, cambiados = sop.sincronizar(texto, canonico)
        assert cambiados == 0, f"{rel} diverge del bloque canonico"
