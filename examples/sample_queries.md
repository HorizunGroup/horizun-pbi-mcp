# DAX query examples (`pbi_run_dax`)

> Requires an active model: run `pbi_list_desktop_models` and
> `pbi_select_model` first.

Table and column names below (`Sales`, `Product[Category]`, …) are placeholders
from a sample model. Replace them with the ones in your own.

## 1. Minimal check
```dax
EVALUATE ROW("ok", 1)
```

## 2. Read a table, with a limit
```dax
EVALUATE TOPN(50, Sales)
```
The tool's `max_rows` also caps the result, but `TOPN` bounds it in the engine,
which is cheaper.

## 3. Evaluate one measure
```dax
EVALUATE ROW("Total Sales", [Total Sales])
```

## 4. A measure broken down by category
```dax
EVALUATE
SUMMARIZECOLUMNS(
    Product[Category],
    "Sales", [Total Sales],
    "Margin", [Margin]
)
```

## 5. Ranking
```dax
EVALUATE
TOPN(
    10,
    SUMMARIZECOLUMNS(Customer[Name], "Sales", [Total Sales]),
    [Sales], DESC
)
ORDER BY [Sales] DESC
```

## 6. Metadata inspection with DMVs
```dax
SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES
```
```dax
SELECT [Name], [Expression] FROM $SYSTEM.TMSCHEMA_MEASURES
```

## 7. Filtering with CALCULATE
```dax
EVALUATE
ROW("Sales 2024", CALCULATE([Total Sales], Calendar[Year] = 2024))
```

## Notes
- DAX engine errors are returned verbatim; they are never swallowed.
- `elapsed_ms` reports execution time.
- If `truncated=true`, raise `max_rows` or narrow the query with `TOPN` or
  filters.
