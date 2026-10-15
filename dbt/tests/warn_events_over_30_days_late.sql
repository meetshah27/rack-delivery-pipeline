-- Data quality (warn): events arriving more than 30 days late (also reported as an audit metric).
{{ config(severity='warn') }}

select
    event_id,
    rack_id,
    late_by_days
from {{ ref('int_rack_events_validated') }}
where late_by_days > 30
