# Horizun PBI MCP v1.0.0

Primera versión estable del repositorio oficial de Horizun PBI MCP.

- 117 tools MCP y contrato compatible con las 34 tools originales.
- 1538 pruebas aprobadas; 3 omitidas únicamente por condiciones externas
  documentadas.
- Validación TMDL/TOM antes de abrir Power BI Desktop, evitando el Frown de
  proyectos con colisiones de nombres.
- Validación PBIR contra esquemas y CLI oficiales cuando están publicados,
  oráculo estructural para `objects`, transacciones atómicas, journals,
  backups y rollback.
- Distribución para Codex y Claude mediante runtime aislado; no se redistribuyen
  DLL de Microsoft ni esquemas de terceros.

## Límites conocidos

- La equivalencia visual completa del bloque `objects` requiere inspección
  renderizada para combinaciones no cubiertas por el oráculo.
- `mode="both"` está bloqueado por diseño: Desktop abierto y escritura PBIP
  segura son precondiciones incompatibles.
- Dos esquemas PBIR que Microsoft aún no publica siguen siendo una limitación
  upstream y se bloquean de forma explícita.
