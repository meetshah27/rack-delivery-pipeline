{%- set columns = [
    'event_id', 'rack_id', 'site_code', 'vendor_code', 'rack_type_code', 'status', 'prev_status',
    'event_at', 'recorded_by', '_ingest_date', '_run_id',
] -%}

with quarantined as (

    select
        {{ columns | join(',\n        ') }},
        quarantine_reason
    from {{ ref('int_rack_events_checked') }}
    where quarantine_reason is not null

)

select
    {{ columns | join(',\n    ') }},
    quarantine_reason as reason,
    '{{ var("run_id") }}' as run_id,
    {{ utc_now() }} as quarantined_at
from quarantined
