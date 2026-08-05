# Soportar visuales personalizados en la escritura de páginas (PBIR)

**Repo:** PowerBI-MCP (v1.2.0)
**Prioridad:** alta — bloquea armar tableros BIM completos desde el MCP
**Reportado:** 2026-08-04, montando el tablero 5D del curso POWERBIM 5

---

## Problema

`pbi_apply_page_spec`, `pbi_create_visual` y `pbi_compose_page` no pueden
escribir visuales PERSONALIZADOS. En un informe que ya tiene instalados el
visor APS de Horizun y el buildMotion, el spec se rechaza:

```
$.visuals[5].type — Tipo no soportado:
'aPSModelViewer02FA0CE460E64F779B012F5831EB091C'.
Soportados: ['actionButton', 'areaChart', ... 'waterfallChart']
```

Consecuencia práctica: en un tablero 4D/5D el MCP monta KPIs, curvas y tablas,
pero NO el visor 3D ni la línea de tiempo — que son justamente el motivo de
conectar BIM con Power BI. Hoy hay que dejar recuadros marcadores y pedirle al
usuario que pegue los visuales a mano.

## Reproducir

1. Abrir un `.pbip` que tenga `CustomVisuals/` con al menos un visual instalado
2. Llamar `pbi_validate_page_spec` con un visual cuyo `type` sea ese GUID
3. Falla en la etapa `schema`

## Causa raíz

`src/services/page_spec.py:179`

```python
elif str(v["type"]).lower() not in visual_factory.TYPE_MAP:
    errores.append(_err(
        f"{base}.type", f"Tipo no soportado: '{v['type']}'.",
        f"Soportados: {visual_factory.SUPPORTED}"))
```

`TYPE_MAP` (`src/pbip/visual_factory.py:82`) se deriva de `REAL_TYPES`, una
tupla fija de 29 tipos nativos, más `ALIASES`. Un GUID de visual personalizado
nunca puede estar ahí porque cada informe instala los suyos.

## Diseño propuesto

No hardcodear: **descubrir**. Cada `.pbip` ya declara sus personalizados en

```
<Report>/CustomVisuals/<GUID>/resources/<GUID>.pbiviz.json
```

y ese archivo trae `capabilities.dataRoles`, o sea los nombres de rol reales.

### 1. Descubrimiento
Nueva función que lea `CustomVisuals/` del informe activo y devuelva
`{guid: [roles]}`. Cachear por ruta + mtime.

### 2. Registro dinámico
Que `TYPE_MAP` se resuelva contra nativos + GUID descubiertos. `SUPPORTED`
debe anunciarlos, para que el mensaje de error liste lo que de verdad se
puede usar en ESE informe.

### 3. Roles verbatim
Para tipos personalizados, saltar `ROLE_MAP`, `REQUIRED_ROLES`,
`MAX_PER_ROLE` y `ROLE_KINDS`: esos contratos se verificaron uno a uno contra
el catálogo oficial de Microsoft y no aplican aquí. El rol se escribe tal cual
en `queryState`.

Validar contra los roles declarados en el `.pbiviz.json` y rechazar los
desconocidos con la lista de válidos — mismo criterio que ya se usa con los
nativos, solo que la fuente de verdad es el manifiesto del visual.

### 4. Preservar `objects`
Al clonar una plantilla existente hay que conservar bloques de configuración
propios del visual. En el visor APS, `objects.connection` lleva `baseUrl` y el
token `mt`: sin eso el visual carga vacío.

Descartar sí los `dataColors` que traen `selector.data`, porque son restos de
una selección puntual del usuario y no configuración reutilizable.

## Criterios de aceptación

- [ ] `pbi_report_capabilities` sigue listando los personalizados presentes
- [ ] `pbi_validate_page_spec` acepta un GUID instalado y rechaza uno que no
- [ ] `pbi_apply_page_spec` escribe un visor APS con sus 4 roles
      (`dbids`, `modelId`, `colorBy`, `externalIds`) y el informe abre
- [ ] `pbi_apply_page_spec` escribe un buildMotion con `timelineDate`
- [ ] El validador oficial (`@microsoft/powerbi-report-authoring-cli`)
      pasa con 0 errores
- [ ] Un rol inexistente para ese GUID se rechaza con mensaje útil
- [ ] Cubierto en `tests/test_generadores_abren.py`

## Prueba de que el enfoque funciona

Se escribieron 4 `visual.json` a mano en un informe real — 3 visores APS y
1 buildMotion, repartidos en 3 páginas — clonando los existentes y ajustando
solo `name` y `position`. El informe abre en Power BI Desktop y los visuales
renderizan con datos.

Eso es exactamente lo que debería generar el MCP: el trabajo consiste en
llevarlo al factory con validación de roles.

### Estructura de referencia (visor APS)

```json
{
  "name": "<id 20 hex>",
  "position": { "x": 24, "y": 257, "width": 816, "height": 250, "z": 0, "tabOrder": 0 },
  "visual": {
    "visualType": "aPSModelViewer02FA0CE460E64F779B012F5831EB091C",
    "query": { "queryState": {
      "dbids":       { "projections": [ { "field": { "Column": { "Expression": { "SourceRef": { "Entity": "Modelo" } }, "Property": "dbids" } }, "queryRef": "Modelo.dbids", "nativeQueryRef": "dbids" } ] },
      "modelId":     { "projections": [ ... "Property": "model_id" ... ] },
      "colorBy":     { "projections": [ ... "Property": "Categoria" ... ] },
      "externalIds": { "projections": [ ... "Property": "external_id" ... ] }
    } },
    "objects": {
      "connection": [ { "properties": {
        "baseUrl": { "expr": { "Literal": { "Value": "'https://pbim.horizunhub.com'" } } },
        "mt":      { "expr": { "Literal": { "Value": "'<token>'" } } }
      } } ]
    },
    "drillFilterOtherVisuals": true
  }
}
```

El buildMotion es el mismo patrón con un solo rol:
`timelineDate` → `Cronograma[Comienzo]`.
