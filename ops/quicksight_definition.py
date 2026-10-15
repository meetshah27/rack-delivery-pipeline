"""Build dashboards/rack_delivery_definition.json: the QuickSight analysis/dashboard definition
for the four sheets: Executive, Cycle Time, SLA Breaches, Pipeline Health.

The definition is generated rather than hand-written: QuickSight's Definition JSON runs to
thousands of lines. Two checks run here, without an AWS account:
- every column a visual uses exists in dashboards/datasets.json (the same file Terraform
  builds the datasets from) or is a calculated field declared below
- the whole definition validates against the QuickSight CreateAnalysis input shape that
  botocore ships (types, required members, unknown keys)

Dataset ARNs are written as ${region}/${account_id} placeholders; quicksight_deploy fills them.

    python -m ops.quicksight_definition
"""

from __future__ import annotations

import json
from itertools import count
from pathlib import Path
from typing import Any

from botocore.session import get_session
from botocore.validate import ParamValidator

ROOT = Path(__file__).resolve().parent.parent
DATASETS: dict[str, Any] = json.loads((ROOT / "dashboards" / "datasets.json").read_text())
OUTPUT = ROOT / "dashboards" / "rack_delivery_definition.json"

DATE_FORMAT = "MMM d, yyyy"
MONTH_FORMAT = "MMM yyyy"
RED, GREEN = "#C8102E", "#2E7D32"

CALCULATED = {
    "ds_delivery": {
        "on_time_flag": (
            "ifelse(isNull({production_at}), NULL, ifelse({is_sla_breached} = 0, 1, 0))"
        ),
        "on_time_target": "0.9",
        "end_to_end_days": "{end_to_end_hours} / 24",
        "in_flight_flag": "ifelse(isNull({production_at}) AND isNull({decommissioned_at}), 1, 0)",
        "breached_completed": "ifelse(isNotNull({production_at}) AND {is_sla_breached} = 1, 1, 0)",
        "days_overdue": 'dateDiff({sla_due_at}, now(), "DD")',
    },
    "ds_pipeline_health": {
        "hours_since_success": (
            'dateDiff(max(ifelse({status} = "SUCCEEDED", {finished_at}, NULL)), now(), "HH")'
        ),
        "quarantine_limit": "0.05",
    },
}

_ids = count(1)


def _id(prefix: str) -> str:
    return f"{prefix}-{next(_ids)}"


def column(ds: str, name: str) -> dict[str, str]:
    if name not in DATASETS[ds]["columns"] and name not in CALCULATED.get(ds, {}):
        raise KeyError(f"{ds} has no column or calculated field {name!r}")
    return {"DataSetIdentifier": ds, "ColumnName": name}


def title(text: str) -> dict[str, Any]:
    return {"Visibility": "VISIBLE", "FormatText": {"PlainText": text}}


def _is_date(ds: str, name: str) -> bool:
    return bool(DATASETS[ds]["columns"].get(name) == "DATETIME")


def dim(
    ds: str, name: str, granularity: str = "DAY", hierarchy: str | None = None
) -> dict[str, Any]:
    field_id = _id(name)
    if _is_date(ds, name):
        field: dict[str, Any] = {
            "DateDimensionField": {
                "FieldId": field_id,
                "Column": column(ds, name),
                "DateGranularity": granularity,
                "FormatConfiguration": {
                    "DateTimeFormat": MONTH_FORMAT
                    if granularity in ("MONTH", "QUARTER")
                    else DATE_FORMAT
                },
            }
        }
        if hierarchy:
            field["DateDimensionField"]["HierarchyId"] = hierarchy
        return field
    field = {"CategoricalDimensionField": {"FieldId": field_id, "Column": column(ds, name)}}
    if hierarchy:
        field["CategoricalDimensionField"]["HierarchyId"] = hierarchy
    return field


def measure(
    ds: str, name: str, agg: str = "SUM", percentile: float | None = None
) -> dict[str, Any]:
    if percentile is not None:
        function: dict[str, Any] = {"PercentileAggregation": {"PercentileValue": percentile}}
    else:
        function = {"SimpleNumericalAggregation": agg}
    return {
        "NumericalMeasureField": {
            "FieldId": _id(name),
            "Column": column(ds, name),
            "AggregationFunction": function,
        }
    }


def field_id(field: dict[str, Any]) -> str:
    inner = next(iter(field.values()))
    return str(inner["FieldId"])


# ---------------------------------------------------------------- visuals


def kpi(
    visual_id: str,
    text: str,
    value: dict[str, Any],
    trend: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    formatting: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    wells: dict[str, Any] = {"Values": [value]}
    if trend:
        wells["TrendGroups"] = [trend]
    if target:
        wells["TargetValues"] = [target]
    visual: dict[str, Any] = {
        "VisualId": visual_id,
        "Title": title(text),
        "ChartConfiguration": {"FieldWells": wells},
    }
    if formatting:
        visual["ConditionalFormatting"] = {"ConditionalFormattingOptions": formatting}
    return {"KPIVisual": visual}


def primary_color(condition: str) -> list[dict[str, Any]]:
    """KPI primary value red when `condition` holds, green otherwise."""
    return [
        {"PrimaryValue": {"TextColor": {"Solid": {"Expression": condition, "Color": RED}}}},
        {
            "PrimaryValue": {
                "TextColor": {"Solid": {"Expression": f"NOT ({condition})", "Color": GREEN}}
            }
        },
    ]


def line(
    visual_id: str,
    text: str,
    category: dict[str, Any],
    values: list[dict[str, Any]],
    colors: list[dict[str, Any]] | None = None,
    reference: float | None = None,
) -> dict[str, Any]:
    wells: dict[str, Any] = {"Category": [category], "Values": values}
    if colors:
        wells["Colors"] = colors
    config: dict[str, Any] = {"FieldWells": {"LineChartAggregatedFieldWells": wells}}
    if reference is not None:
        config["ReferenceLines"] = [
            {
                "DataConfiguration": {"StaticConfiguration": {"Value": reference}},
                "StyleConfiguration": {"Pattern": "DASHED", "Color": RED},
                "LabelConfiguration": {
                    "CustomLabelConfiguration": {"CustomLabel": f"{reference:.0%} limit"}
                },
            }
        ]
    return {
        "LineChartVisual": {
            "VisualId": visual_id,
            "Title": title(text),
            "ChartConfiguration": config,
        }
    }


def bar(
    visual_id: str,
    text: str,
    category: dict[str, Any],
    values: list[dict[str, Any]],
    colors: list[dict[str, Any]] | None = None,
    stacked: bool = False,
    hierarchy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wells: dict[str, Any] = {"Category": [category], "Values": values}
    if colors:
        wells["Colors"] = colors
    config: dict[str, Any] = {
        "FieldWells": {"BarChartAggregatedFieldWells": wells},
        "Orientation": "HORIZONTAL",
        "BarsArrangement": "STACKED" if stacked else "CLUSTERED",
    }
    visual: dict[str, Any] = {
        "VisualId": visual_id,
        "Title": title(text),
        "ChartConfiguration": config,
    }
    if hierarchy:
        visual["ColumnHierarchies"] = [hierarchy]
    return {"BarChartVisual": visual}


def table(
    visual_id: str,
    text: str,
    group_by: list[dict[str, Any]],
    values: list[dict[str, Any]],
    sort_field: str | None = None,
    descending: bool = True,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "FieldWells": {"TableAggregatedFieldWells": {"GroupBy": group_by, "Values": values}}
    }
    if sort_field:
        config["SortConfiguration"] = {
            "RowSort": [
                {"FieldSort": {"FieldId": sort_field, "Direction": "DESC" if descending else "ASC"}}
            ]
        }
    return {
        "TableVisual": {"VisualId": visual_id, "Title": title(text), "ChartConfiguration": config}
    }


def grid(elements: list[tuple[str, str, int, int]]) -> list[dict[str, Any]]:
    """Lay elements out left to right in a 36-column grid: (id, type, column span, row span)."""
    placed, column_index, row_index, row_height = [], 0, 0, 0
    for element_id, element_type, span, height in elements:
        if column_index + span > 36:
            column_index, row_index, row_height = 0, row_index + row_height, 0
        placed.append(
            {
                "ElementId": element_id,
                "ElementType": element_type,
                "ColumnIndex": column_index,
                "ColumnSpan": span,
                "RowIndex": row_index,
                "RowSpan": height,
            }
        )
        column_index += span
        row_height = max(row_height, height)
    return [{"Configuration": {"GridLayout": {"Elements": placed}}}]


# ---------------------------------------------------------------- sheets


def executive() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = "ds_delivery"
    on_time = measure(d, "on_time_flag", "AVERAGE")
    visuals = [
        kpi(
            "exec-on-time",
            "What share of racks reached production within SLA this month, vs last month?",
            on_time,
            trend=dim(d, "production_at", "MONTH"),
            target=measure(d, "on_time_target", "AVERAGE"),
            formatting=primary_color("AVG({on_time_flag}) < 0.9"),
        ),
        kpi(
            "exec-p90",
            "How many days does the slowest 10% take, order to production?",
            measure(d, "end_to_end_days", percentile=90),
            trend=dim(d, "production_at", "MONTH"),
        ),
        kpi(
            "exec-in-flight",
            "How many racks are ordered but not yet in production?",
            measure(d, "in_flight_flag"),
        ),
        kpi(
            "exec-overdue",
            "How many open racks are past their SLA due date?",
            measure(d, "is_overdue_open"),
            formatting=primary_color("SUM({is_overdue_open}) > 0"),
        ),
        line(
            "exec-vendor-trend",
            "How has each vendor's on-time rate moved over the last 12 months?",
            dim(d, "production_at", "MONTH"),
            [measure(d, "on_time_flag", "AVERAGE")],
            colors=[dim(d, "vendor_name")],
        ),
        {
            "ComboChartVisual": {
                "VisualId": "exec-throughput",
                "Title": title("How many racks reach production each week, and what is the trend?"),
                "ChartConfiguration": {
                    "FieldWells": {
                        "ComboChartAggregatedFieldWells": {
                            "Category": [dim("ds_throughput", "date", "WEEK")],
                            "BarValues": [measure("ds_throughput", "racks_to_production")],
                            "LineValues": [measure("ds_throughput", "racks_to_production_7d_avg")],
                        }
                    }
                },
            }
        },
    ]
    sheet = {
        "SheetId": "executive",
        "Name": "Executive",
        "Visuals": visuals,
        "TextBoxes": [
            {
                "SheetTextBoxId": "exec-note",
                "Content": (
                    "<text-box>Data refreshes daily after the 06:00 UTC pipeline run; the Pipeline "
                    "Health sheet shows the last successful run. On-time means production by "
                    "the SLA "
                    "of the contract version in effect when the rack was ordered. The data is "
                    "synthetic, generated by the project's simulator.</text-box>"
                ),
            }
        ],
        "Layouts": grid(
            [
                ("exec-on-time", "VISUAL", 9, 6),
                ("exec-p90", "VISUAL", 9, 6),
                ("exec-in-flight", "VISUAL", 9, 6),
                ("exec-overdue", "VISUAL", 9, 6),
                ("exec-vendor-trend", "VISUAL", 18, 12),
                ("exec-throughput", "VISUAL", 18, 12),
                ("exec-note", "TEXT_BOX", 36, 3),
            ]
        ),
    }
    filters = [
        {
            "FilterGroupId": "fg-last-12-months",
            "CrossDataset": "SINGLE_DATASET",
            "Status": "ENABLED",
            "Filters": [
                {
                    "RelativeDatesFilter": {
                        "FilterId": "f-last-12-months",
                        "Column": column(d, "production_at"),
                        "AnchorDateConfiguration": {"AnchorOption": "NOW"},
                        "TimeGranularity": "MONTH",
                        "RelativeDateType": "LAST",
                        "RelativeDateValue": 12,
                        "NullOption": "NON_NULLS_ONLY",
                    }
                }
            ],
            "ScopeConfiguration": {
                "SelectedSheets": {
                    "SheetVisualScopingConfigurations": [
                        {
                            "SheetId": "executive",
                            "Scope": "SELECTED_VISUALS",
                            "VisualIds": ["exec-vendor-trend"],
                        }
                    ]
                }
            },
        }
    ]
    return sheet, filters


def cycle_time() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = "ds_delivery"
    stages = [
        measure(d, name, "MEDIAN")
        for name in ("vendor_lead_hours", "transit_hours", "install_hours", "burn_in_hours")
    ]
    visuals = [
        bar(
            "cycle-stages",
            "Where does the time go, stage by stage, for each vendor? (median hours)",
            dim(d, "vendor_name"),
            stages,
            stacked=True,
        ),
        {
            "BoxPlotVisual": {
                "VisualId": "cycle-site-box",
                "Title": title("How spread out is order-to-production time at each site? (days)"),
                "ChartConfiguration": {
                    "FieldWells": {
                        "BoxPlotAggregatedFieldWells": {
                            "GroupBy": [dim(d, "site_code")],
                            "Values": [measure(d, "end_to_end_days", "AVERAGE")],
                        }
                    }
                },
            }
        },
        {
            "HeatMapVisual": {
                "VisualId": "cycle-install-heat",
                "Title": title("Which sites and months had the slowest installs? (p90 hours)"),
                "ChartConfiguration": {
                    "FieldWells": {
                        "HeatMapAggregatedFieldWells": {
                            "Rows": [dim(d, "site_code")],
                            "Columns": [dim(d, "cabled_at", "MONTH")],
                            "Values": [measure(d, "install_hours", percentile=90)],
                        }
                    }
                },
            }
        },
    ]
    filter_columns = {
        "vendor_name": "Vendor",
        "site_code": "Site",
        "rack_type_description": "Rack type",
    }
    filters, controls = [], []
    for name, label in filter_columns.items():
        filter_id = f"f-cycle-{name}"
        filters.append(
            {
                "FilterGroupId": f"fg-cycle-{name}",
                "CrossDataset": "SINGLE_DATASET",
                "Status": "ENABLED",
                "Filters": [
                    {
                        "CategoryFilter": {
                            "FilterId": filter_id,
                            "Column": column(d, name),
                            "Configuration": {
                                "FilterListConfiguration": {
                                    "MatchOperator": "CONTAINS",
                                    "SelectAllOptions": "FILTER_ALL_VALUES",
                                }
                            },
                        }
                    }
                ],
                "ScopeConfiguration": {
                    "SelectedSheets": {
                        "SheetVisualScopingConfigurations": [
                            {"SheetId": "cycle-time", "Scope": "ALL_VISUALS"}
                        ]
                    }
                },
            }
        )
        controls.append(
            {
                "Dropdown": {
                    "FilterControlId": f"c-cycle-{name}",
                    "Title": label,
                    "SourceFilterId": filter_id,
                    "Type": "MULTI_SELECT",
                }
            }
        )
    filters.append(
        {
            "FilterGroupId": "fg-cycle-order-month",
            "CrossDataset": "SINGLE_DATASET",
            "Status": "ENABLED",
            "Filters": [
                {
                    "TimeRangeFilter": {
                        "FilterId": "f-cycle-order-month",
                        "Column": column(d, "ordered_at"),
                        "NullOption": "NON_NULLS_ONLY",
                        "TimeGranularity": "MONTH",
                    }
                }
            ],
            "ScopeConfiguration": {
                "SelectedSheets": {
                    "SheetVisualScopingConfigurations": [
                        {"SheetId": "cycle-time", "Scope": "ALL_VISUALS"}
                    ]
                }
            },
        }
    )
    controls.append(
        {
            "DateTimePicker": {
                "FilterControlId": "c-cycle-order-month",
                "Title": "Order month",
                "SourceFilterId": "f-cycle-order-month",
                "Type": "DATE_RANGE",
            }
        }
    )
    sheet = {
        "SheetId": "cycle-time",
        "Name": "Cycle Time",
        "Visuals": visuals,
        "FilterControls": controls,
        "Layouts": grid(
            [
                ("cycle-stages", "VISUAL", 36, 12),
                ("cycle-site-box", "VISUAL", 18, 12),
                ("cycle-install-heat", "VISUAL", 18, 12),
            ]
        ),
    }
    return sheet, filters


def sla_breaches() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    d = "ds_delivery"
    days_overdue = measure(d, "days_overdue", "MAX")
    hierarchy = {
        "ExplicitHierarchy": {
            "HierarchyId": "h-vendor-site-rack",
            "Columns": [column(d, "vendor_name"), column(d, "site_code"), column(d, "rack_id")],
        }
    }
    visuals = [
        table(
            "sla-overdue",
            "Which open racks are overdue, and by how many days?",
            [dim(d, name) for name in ("rack_id", "site_code", "vendor_name", "current_status")],
            [measure(d, "days_in_current_status", "MAX"), days_overdue],
            sort_field=field_id(days_overdue),
        ),
        bar(
            "sla-breaches",
            "Which vendors and contract tiers miss SLA most? (drill: vendor > site > rack)",
            dim(d, "vendor_name", hierarchy="h-vendor-site-rack"),
            [measure(d, "breached_completed")],
            colors=[dim(d, "contract_tier")],
            hierarchy=hierarchy,
        ),
    ]
    filters = [
        {
            "FilterGroupId": "fg-overdue-only",
            "CrossDataset": "SINGLE_DATASET",
            "Status": "ENABLED",
            "Filters": [
                {
                    "NumericEqualityFilter": {
                        "FilterId": "f-overdue-only",
                        "Column": column(d, "is_overdue_open"),
                        "Value": 1,
                        "MatchOperator": "EQUALS",
                        "NullOption": "NON_NULLS_ONLY",
                    }
                }
            ],
            "ScopeConfiguration": {
                "SelectedSheets": {
                    "SheetVisualScopingConfigurations": [
                        {
                            "SheetId": "sla-breaches",
                            "Scope": "SELECTED_VISUALS",
                            "VisualIds": ["sla-overdue"],
                        }
                    ]
                }
            },
        }
    ]
    sheet = {
        "SheetId": "sla-breaches",
        "Name": "SLA Breaches",
        "Visuals": visuals,
        "Layouts": grid([("sla-overdue", "VISUAL", 20, 14), ("sla-breaches", "VISUAL", 16, 14)]),
    }
    return sheet, filters


def pipeline_health() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    p = "ds_pipeline_health"
    run_columns = [
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "ingest_date_min",
        "ingest_date_max",
    ]
    numbers = [
        "rows_landed",
        "rows_loaded_raw",
        "rows_quarantined",
        "rows_fct_event_merged",
        "late_event_count",
        "dbt_tests_passed",
        "dbt_tests_warned",
        "dbt_tests_failed",
        "redshift_charged_seconds",
        "est_cost_usd",
    ]
    last_started = measure(p, "redshift_charged_seconds")
    visuals = [
        table(
            "health-last-run",
            "Did the last run succeed, and when did it finish?",
            [dim(p, "status"), dim(p, "finished_at")],
            [last_started],
        ),
        kpi(
            "health-hours",
            "How many hours since the last successful run?",
            {
                "NumericalMeasureField": {
                    "FieldId": _id("hours_since_success"),
                    "Column": column(p, "hours_since_success"),
                }
            },
            formatting=primary_color("{hours_since_success} > 26"),
        ),
        line(
            "health-quarantine",
            "Is the quarantine rate per run under 5%?",
            dim(p, "started_at"),
            [measure(p, "quarantine_rate", "MAX")],
            reference=0.05,
        ),
        line(
            "health-cost",
            "What does each run cost?",
            dim(p, "started_at"),
            [measure(p, "est_cost_usd", "SUM")],
        ),
        table(
            "health-runs",
            "What happened in the last 14 runs?",
            [dim(p, name) for name in run_columns],
            [measure(p, name) for name in numbers],
        ),
    ]
    filters = [
        {
            "FilterGroupId": "fg-last-run",
            "CrossDataset": "SINGLE_DATASET",
            "Status": "ENABLED",
            "Filters": [
                {
                    "TopBottomFilter": {
                        "FilterId": "f-last-run",
                        "Column": column(p, "run_id"),
                        "Limit": 1,
                        "AggregationSortConfigurations": [
                            {
                                "Column": column(p, "started_at"),
                                "SortDirection": "DESC",
                                "AggregationFunction": {"DateAggregationFunction": "MAX"},
                            }
                        ],
                    }
                }
            ],
            "ScopeConfiguration": {
                "SelectedSheets": {
                    "SheetVisualScopingConfigurations": [
                        {
                            "SheetId": "pipeline-health",
                            "Scope": "SELECTED_VISUALS",
                            "VisualIds": ["health-last-run"],
                        }
                    ]
                }
            },
        },
        {
            "FilterGroupId": "fg-last-14",
            "CrossDataset": "SINGLE_DATASET",
            "Status": "ENABLED",
            "Filters": [
                {
                    "TopBottomFilter": {
                        "FilterId": "f-last-14",
                        "Column": column(p, "run_id"),
                        "Limit": 14,
                        "AggregationSortConfigurations": [
                            {
                                "Column": column(p, "started_at"),
                                "SortDirection": "DESC",
                                "AggregationFunction": {"DateAggregationFunction": "MAX"},
                            }
                        ],
                    }
                }
            ],
            "ScopeConfiguration": {
                "SelectedSheets": {
                    "SheetVisualScopingConfigurations": [
                        {
                            "SheetId": "pipeline-health",
                            "Scope": "SELECTED_VISUALS",
                            "VisualIds": ["health-runs"],
                        }
                    ]
                }
            },
        },
    ]
    sheet = {
        "SheetId": "pipeline-health",
        "Name": "Pipeline Health",
        "Visuals": visuals,
        "Layouts": grid(
            [
                ("health-last-run", "VISUAL", 18, 6),
                ("health-hours", "VISUAL", 18, 6),
                ("health-quarantine", "VISUAL", 18, 10),
                ("health-cost", "VISUAL", 18, 10),
                ("health-runs", "VISUAL", 36, 12),
            ]
        ),
    }
    return sheet, filters


def build() -> dict[str, Any]:
    global _ids
    _ids = count(1)  # field ids restart, so every build is byte-identical
    sheets, filter_groups = [], []
    for make_sheet in (executive, cycle_time, sla_breaches, pipeline_health):
        sheet, filters = make_sheet()
        sheets.append(sheet)
        filter_groups += filters
    return {
        "DataSetIdentifierDeclarations": [
            {
                "Identifier": ds,
                "DataSetArn": f"arn:aws:quicksight:${{region}}:${{account_id}}:dataset/{ds}",
            }
            for ds in DATASETS
            if ds != "ds_rls_permissions"
        ],
        "Sheets": sheets,
        "CalculatedFields": [
            {"DataSetIdentifier": ds, "Name": name, "Expression": expression}
            for ds, fields in CALCULATED.items()
            for name, expression in fields.items()
        ],
        "FilterGroups": filter_groups,
        "AnalysisDefaults": {
            "DefaultNewSheetConfiguration": {
                "InteractiveLayoutConfiguration": {
                    "Grid": {
                        "CanvasSizeOptions": {
                            "ScreenCanvasSizeOptions": {
                                "ResizeOption": "FIXED",
                                "OptimizedViewPortWidth": "1600px",
                            }
                        }
                    }
                },
                "SheetContentType": "INTERACTIVE",
            }
        },
    }


def validate(definition: dict[str, Any]) -> None:
    """Raise if the definition does not fit QuickSight's CreateAnalysis input shape."""
    model = get_session().get_service_model("quicksight")
    shape = model.operation_model("CreateAnalysis").input_shape
    params = {
        "AwsAccountId": "123456789012",
        "AnalysisId": "rack-delivery",
        "Name": "Rack delivery",
        "Definition": definition,
    }
    report = ParamValidator().validate(params, shape)
    if report.has_errors():
        raise ValueError(report.generate_report())


def main() -> None:
    definition = build()
    validate(definition)
    OUTPUT.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8", newline="\n")
    visuals = sum(len(sheet["Visuals"]) for sheet in definition["Sheets"])
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: {len(definition['Sheets'])} sheets, {visuals} visuals"
    )


if __name__ == "__main__":
    main()
