# Ejemplos de consultas DAX (`pbi_run_dax`)

> Requiere un modelo activo: primero `pbi_list_desktop_models` y `pbi_select_model`.

## 1. Prueba mínima
```dax
EVALUATE ROW("ok", 1)
```

## 2. Ver todas las filas de una tabla (con límite)
```dax
EVALUATE TOPN(50, Ventas)
```
`max_rows` en el tool también limita; usa TOPN para acotar en el motor.

## 3. Una medida evaluada
```dax
EVALUATE ROW("Total Ventas", [Total Ventas])
```

## 4. Medida por categoría
```dax
EVALUATE
SUMMARIZECOLUMNS(
    Producto[Categoria],
    "Ventas", [Total Ventas],
    "Margen", [Margen]
)
```

## 5. Ranking
```dax
EVALUATE
TOPN(
    10,
    SUMMARIZECOLUMNS(Cliente[Nombre], "Ventas", [Total Ventas]),
    [Ventas], DESC
)
ORDER BY [Ventas] DESC
```

## 6. Inspección de metadatos con DMVs
```dax
SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES
```
```dax
SELECT [Name], [Expression] FROM $SYSTEM.TMSCHEMA_MEASURES
```

## 7. Filtro con CALCULATE
```dax
EVALUATE
ROW("Ventas 2024", CALCULATE([Total Ventas], Calendario[Anio] = 2024))
```

## Notas
- Los errores del motor DAX se devuelven tal cual (no se ocultan).
- `elapsed_ms` reporta el tiempo de ejecución.
- Si `truncated=true`, aumenta `max_rows` o acota con TOPN/filtros.
