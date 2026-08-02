# Horizun PBI MCP v1.0.1

Primera actualización correctiva de la versión estable. Mantiene intacto el
contrato de las 117 tools y cierra cuatro huecos de validación detectados tras
publicar `v1.0.0`.

## Corregido

- El oráculo oficial revisa también visuales que solo contienen
  `visualContainerObjects`.
- Las expresiones de formato vacías (`expr: {}`) se rechazan antes de escribir.
- La degradación a una versión anterior del esquema PBIR solo se permite para
  URLs que el manifiesto identifica expresamente como no publicadas por
  Microsoft.
- Una sesión PBIP ya abierta se reutiliza antes de validar el estado guardado
  en disco, evitando bloquear un modelo válido que Desktop ya sirve en memoria.

## Evidencia

- 117 tools; contrato MCP sin cambios.
- 1547 pruebas aprobadas y 3 omitidas por precondiciones externas documentadas.
- CI verde en Windows con Python 3.10 y 3.13.
- Wheel y sdist construidos y verificados con `twine check`.
- `scripts/doctor.py` operativo con DLL, esquemas PBIR y CLI oficial presentes.
