# Documentacion del modelo — Ventas (ejemplo)

_Este es un ejemplo del tipo de salida que genera `pbi_document_model`._

## Resumen

- **Modelo:** Ventas
- **Cultura:** es-ES
- **Tablas:** 4
- **Medidas:** 6
- **Relaciones:** 3
- **Jerarquias:** 1
- **Roles (RLS):** 1

## Tablas

| Tabla | Oculta | Columnas | Medidas | Fecha |
|---|---|---|---|---|
| Ventas | no | 8 | 6 | |
| Producto | no | 5 | 0 | |
| Cliente | no | 6 | 0 | |
| Calendario | no | 7 | 0 | si |

### Ventas

| Columna | Tipo dato | Tipo | Oculta | SummarizeBy |
|---|---|---|---|---|
| VentaID | Int64 | Data | si | none |
| Monto | Decimal | Data | no | sum |
| Fecha | DateTime | Data | no | none |

## Medidas

### Ventas[Total Ventas] _(carpeta: KPIs)_
- Formato: `#,0`

```dax
SUM(Ventas[Monto])
```

### Ventas[Margen] _(carpeta: KPIs)_
- Formato: `0.0%`

```dax
DIVIDE([Utilidad], [Total Ventas])
```

## Relaciones

| Desde | Hacia | Cardinalidad | Filtro cruzado | Activa |
|---|---|---|---|---|
| Ventas[ProductoID] | Producto[ProductoID] | Many-One | OneDirection | si |
| Ventas[ClienteID] | Cliente[ClienteID] | Many-One | OneDirection | si |
| Ventas[Fecha] | Calendario[Fecha] | Many-One | OneDirection | si |

## Jerarquias

- **Calendario[Calendario]**: Anio > Trimestre > Mes

## Roles / Seguridad a nivel de fila (RLS)

### Vendedor
- `Ventas`: `Ventas[Vendedor] = USERPRINCIPALNAME()`

## Advertencias de calidad del modelo

Total: 2 ({'info': 1, 'warning': 1})

- **[warning] relacion_bidireccional** — Relacion bidireccional Ventas <-> Producto ...
- **[info] id_visible** — Columna de ID visible 'Cliente[ClienteID]' ...
