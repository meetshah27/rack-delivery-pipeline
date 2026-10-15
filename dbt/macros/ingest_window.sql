{#-
    Condition selecting events ingested in the incremental window. Facts recompute every rack
    with such an event, not just the new events, which keeps prev_status, durations and
    milestones right when a late event lands between events already loaded.

    Window: the backfill vars start_date/end_date when given, otherwise the last
    `lookback_days` of ingest dates already in the target (`this_ingest_column` of {{ this }}).
-#}
{% macro ingest_window(ingest_column, this_ingest_column) -%}
    {%- if var('start_date') is not none -%}
        {{ ingest_column }} between cast('{{ var("start_date") }}' as date)
        and cast('{{ var("end_date") }}' as date)
    {%- else -%}
        {{ ingest_column }} >= (select {{ dbt.dateadd('day', -var('lookback_days'), 'max(' ~ this_ingest_column ~ ')') }} from {{ this }})
    {%- endif -%}
{%- endmacro %}
