-- SPEC 6 (warn): events more than 1 hour after their load time are quarantined.
{{ config(severity='warn') }}

select
    event_id,
    rack_id,
    event_at
from {{ ref('quarantine_rack_events') }}
where reason = 'future_dated'
