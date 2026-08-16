# Model documentation — Sales (example)

_This is an example of the output `pbi_document_model` produces._

## Summary

- **Model:** Sales
- **Culture:** en-US
- **Tables:** 4
- **Measures:** 6
- **Relationships:** 3
- **Hierarchies:** 1
- **Roles (RLS):** 1

## Tables

| Table | Hidden | Columns | Measures | Date |
|---|---|---|---|---|
| Sales | no | 8 | 6 | |
| Product | no | 5 | 0 | |
| Customer | no | 6 | 0 | |
| Calendar | no | 7 | 0 | yes |

### Sales

| Column | Data type | Type | Hidden | SummarizeBy |
|---|---|---|---|---|
| SaleID | Int64 | Data | yes | none |
| Amount | Decimal | Data | no | sum |
| Date | DateTime | Data | no | none |

## Measures

### Sales[Total Sales] _(folder: KPIs)_
- Format: `#,0`

```dax
SUM(Sales[Amount])
```

### Sales[Margin] _(folder: KPIs)_
- Format: `0.0%`

```dax
DIVIDE([Profit], [Total Sales])
```

## Relationships

| From | To | Cardinality | Cross filter | Active |
|---|---|---|---|---|
| Sales[ProductID] | Product[ProductID] | Many-One | OneDirection | yes |
| Sales[CustomerID] | Customer[CustomerID] | Many-One | OneDirection | yes |
| Sales[Date] | Calendar[Date] | Many-One | OneDirection | yes |

## Hierarchies

- **Calendar[Calendar]**: Year > Quarter > Month

## Roles / Row-level security (RLS)

### Salesperson
- `Sales`: `Sales[Salesperson] = USERPRINCIPALNAME()`

## Model quality warnings

Total: 2 ({'info': 1, 'warning': 1})

- **[warning] bidirectional_relationship** — Bidirectional relationship Sales <-> Product ...
- **[info] visible_id** — Visible ID column 'Customer[CustomerID]' ...
