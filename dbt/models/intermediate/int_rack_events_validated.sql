{%- set columns = [
    'event_id', 'rack_id', 'site_code', 'vendor_code', 'rack_type_code', 'status', 'prev_status',
    'event_at', 'prev_event_at', 'recorded_by', 'is_regression', 'is_gap_bridged', 'late_by_days',
    '_ingest_date', '_run_id', '_loaded_at',
] -%}

with checked as (

    select
        {{ columns | join(',\n        ') }}
    from {{ ref('int_rack_events_checked') }}
    where quarantine_reason is null

)

select
    {{ columns | join(',\n    ') }},
    cast(
        {{ dbt.datediff('prev_event_at', 'event_at', 'second') }} / 3600.0 as decimal(10, 2)  -- noqa: LT05
    ) as hours_since_prev_event
from checked
