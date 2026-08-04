"""El puerto del ecosistema: un CONTRATO de datos, no un bus de APIs.

Fase 4 de la vision, con la decision de arquitectura ya tomada (2026-08-03):
Revit, Navisworks y Project NO se conectan en vivo entre si -cuatro
aplicaciones de escritorio encadenadas por APIs es fragil de una forma que no
se arregla-. Lo que ya funciona en los proyectos reales es una LLAVE
SEMANTICA compartida (`HRZ_COD_PRES`): cada herramienta EMITE un dataset
normalizado y este MCP lo consume.

El puerto es entonces un documento: que datasets existen, con que columnas y
tipos, y cual es la llave de cada uno. Vive versionado junto al `.pbip`
(`pbi-port-contract.json`), igual que el brief, y este modulo hace las dos
mitades del apreton de manos:

- **Validar un archivo entrante** (el export de Revit/Navisworks/Project)
  ANTES de cargarlo: columnas que faltan, tipos que no cuadran, llave
  ausente. Estructural y honesto: lo que exige leer los datos completos
  (llaves duplicadas, huerfanas) es trabajo de `pbi_diagnose_data` con la
  tabla ya cargada, y la respuesta lo dice en vez de fingir que lo comprobo.
- **Validar el modelo activo** contra el contrato: que cada dataset este como
  tabla, con sus columnas y tipos. Y regalar la integracion que cierra el
  circulo: las llaves del contrato, listas como `critical_fields` para el
  brief -asi el diagnostico las trata como criticas sin teclearlas dos veces-.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

log = get_logger("port_contract")

CONTRACT_FILENAME = "pbi-port-contract.json"
SCHEMA_VERSION = "1.0"

_TIPOS = ("string", "int64", "double", "decimal", "dateTime", "boolean")

#: Compatibilidad de tipo INFERIDO del archivo -> tipo DECLARADO en contrato.
#: `int64` satisface `double`/`decimal` (todo entero es un decimal valido);
#: al reves NO: un double en una columna declarada int64 es perdida de datos.
_COMPATIBLES = {
    ("int64", "double"), ("int64", "decimal"), ("int64", "int64"),
    ("double", "double"), ("double", "decimal"), ("decimal", "decimal"),
    ("decimal", "double"), ("string", "string"), ("boolean", "boolean"),
    ("dateTime", "dateTime"),
}


class ContractError(PowerBIMCPError):
    code = "port_contract_error"


def contract_path(active) -> Path:
    return Path(active.project_dir) / CONTRACT_FILENAME


# ------------------------------------------------------------- validacion ---
def validate_contract(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida el contrato. Devuelve la forma canonica."""
    if not isinstance(datos, dict):
        raise ValidationError("El contrato debe ser un objeto.")
    datasets = datos.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValidationError(
            "El contrato necesita 'datasets': [{name, key, columns}]. Cada "
            "herramienta del ecosistema emite uno y esa lista es el puerto.")

    canonicos: List[Dict[str, Any]] = []
    nombres = set()
    for i, d in enumerate(datasets):
        base = f"datasets[{i}]"
        if not isinstance(d, dict) or not str(d.get("name") or "").strip():
            raise ValidationError(f"{base} necesita 'name'.")
        nombre = str(d["name"]).strip()
        if nombre.casefold() in nombres:
            raise ValidationError(f"Dataset duplicado: '{nombre}'.")
        nombres.add(nombre.casefold())

        columnas = d.get("columns")
        if not isinstance(columnas, list) or not columnas:
            raise ValidationError(f"{base} necesita 'columns' [{{name,type}}].")
        cols_canon, vistos = [], set()
        for j, c in enumerate(columnas):
            if not isinstance(c, dict) or not str(c.get("name") or "").strip():
                raise ValidationError(f"{base}.columns[{j}] necesita 'name'.")
            tipo = str(c.get("type") or "string")
            if tipo not in _TIPOS:
                raise ValidationError(
                    f"{base}.columns[{j}].type '{tipo}' no es un tipo TMDL. "
                    f"Usa: {list(_TIPOS)}.")
            cn = str(c["name"]).strip()
            if cn.casefold() in vistos:
                raise ValidationError(f"{base}: columna duplicada '{cn}'.")
            vistos.add(cn.casefold())
            entrada = {"name": cn, "type": tipo}
            if c.get("required"):
                entrada["required"] = True
            cols_canon.append(entrada)

        llave = d.get("key")
        llaves = [llave] if isinstance(llave, str) else list(llave or [])
        for k in llaves:
            if str(k).casefold() not in vistos:
                raise ValidationError(
                    f"{base}: la llave '{k}' no esta entre sus columnas.")
        if not llaves:
            raise ValidationError(
                f"{base} necesita 'key': sin llave no hay contrato -es lo que "
                "permite cruzar los datasets del ecosistema entre si-.")

        canon = {"name": nombre, "key": llaves, "columns": cols_canon}
        if d.get("emitted_by"):
            canon["emitted_by"] = str(d["emitted_by"]).strip()
        if d.get("description"):
            canon["description"] = str(d["description"]).strip()
        canonicos.append(canon)

    salida = {"schema_version": SCHEMA_VERSION, "datasets": canonicos}
    if datos.get("name"):
        salida["name"] = str(datos["name"]).strip()
    return salida


def read_contract(active) -> Optional[Dict[str, Any]]:
    ruta = contract_path(active)
    if not ruta.exists():
        return None
    from horizun_pbi_mcp.utils.json_utils import read_json

    datos = read_json(ruta)
    return validate_contract(datos)


def write_contract(active, datos: Dict[str, Any]) -> Dict[str, Any]:
    from horizun_pbi_mcp.services import txn as txn_service

    canonico = validate_contract(datos)
    ruta = contract_path(active)
    existia = ruta.exists()
    cm = txn_service.project_transaction(active, [ruta],
                                         tool="pbi_define_port_contract")
    with cm as tx:
        tx.write_json(ruta, canonico)
    return {"contract": canonico, "path": str(ruta),
            "created": not existia, "updated": existia,
            "transaction": cm.result}


# -------------------------------------------------------------- chequeos ---
def _dataset(contrato: Dict[str, Any], nombre: str) -> Dict[str, Any]:
    for d in contrato["datasets"]:
        if d["name"].casefold() == str(nombre).casefold():
            return d
    raise ValidationError(
        f"El contrato no tiene el dataset '{nombre}'. Tiene: "
        f"{[d['name'] for d in contrato['datasets']]}.")


def check_file(contrato: Dict[str, Any], dataset: str,
               source_path: str) -> Dict[str, Any]:
    """Un archivo entrante contra su dataset del contrato. ESTRUCTURAL."""
    from horizun_pbi_mcp.pbip.table_from_file import perfilar

    spec = _dataset(contrato, dataset)
    perfil = perfilar(source_path)
    inferidas = {c["name"].casefold(): c["data_type"]
                 for c in perfil["columns"]}
    declaradas = {c["name"].casefold(): c for c in spec["columns"]}

    faltan = [c["name"] for c in spec["columns"]
              if c["name"].casefold() not in inferidas]
    extras = [c["name"] for c in perfil["columns"]
              if c["name"].casefold() not in declaradas]
    tipos_mal = []
    for c in spec["columns"]:
        inf = inferidas.get(c["name"].casefold())
        if inf is None:
            continue
        if (inf, c["type"]) not in _COMPATIBLES:
            tipos_mal.append({"column": c["name"], "declared": c["type"],
                              "inferred": inf})
    llaves_ausentes = [k for k in spec["key"]
                       if str(k).casefold() not in inferidas]

    conforme = not faltan and not tipos_mal and not llaves_ausentes
    return {
        "dataset": spec["name"], "source": str(source_path),
        "conformant": conforme,
        "missing_columns": faltan,
        "extra_columns": extras,       # informativo: extra no rompe el puerto
        "type_mismatches": tipos_mal,
        "missing_keys": llaves_ausentes,
        "checked": "structure (columns, types, keys present)",
        "not_checked": ("key uniqueness/blanks and referential integrity "
                        "need the FULL data: load the table and run "
                        "pbi_diagnose_data. This check reads structure only "
                        "and saying otherwise would be pretending."),
    }


def check_model(contrato: Dict[str, Any],
                model_data: Dict[str, Any]) -> Dict[str, Any]:
    """El modelo activo contra el contrato, y el regalo del circulo cerrado:
    las llaves del contrato como `critical_fields` listos para el brief."""
    tablas = {str(t.get("name", "")).casefold(): t
              for t in model_data.get("tables") or []}
    resultados = []
    for spec in contrato["datasets"]:
        t = tablas.get(spec["name"].casefold())
        if t is None:
            resultados.append({"dataset": spec["name"], "present": False,
                               "missing_columns": [c["name"]
                                                   for c in spec["columns"]]})
            continue
        cols = {str(c.get("name", "")).casefold(): str(c.get("data_type") or "")
                for c in t.get("columns") or []}
        faltan = [c["name"] for c in spec["columns"]
                  if c["name"].casefold() not in cols]
        tipos_mal = [{"column": c["name"], "declared": c["type"],
                      "model": cols[c["name"].casefold()]}
                     for c in spec["columns"]
                     if c["name"].casefold() in cols
                     and (cols[c["name"].casefold()], c["type"]) not in _COMPATIBLES
                     and cols[c["name"].casefold()] != c["type"]]
        resultados.append({"dataset": spec["name"], "present": True,
                           "missing_columns": faltan,
                           "type_mismatches": tipos_mal})

    sugeridos = [{"field": f"{d['name']}[{k}]",
                  "why": f"llave del puerto ({d['name']})"}
                 for d in contrato["datasets"] for k in d["key"]]
    conforme = all(r.get("present") and not r.get("missing_columns")
                   and not r.get("type_mismatches") for r in resultados)
    return {
        "conformant": conforme,
        "datasets": resultados,
        "suggested_critical_fields": sugeridos,
        "next": ("Pass suggested_critical_fields to pbi_define_brief and run "
                 "pbi_diagnose_data: the port keys become owner-critical and "
                 "orphan/duplicate findings on them escalate to error."),
    }
