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
    {{ hours_between('prev_event_at', 'event_at') }} as hours_since_prev_event  -- noqa: LT05
from checked
