-- SPEC 6 (warn): transitions missing from the status_transitions seed are quarantined.
{{ config(severity='warn') }}

select
    event_id,
    rack_id,
    prev_status,
    status
from {{ ref('quarantine_rack_events') }}
where reason = 'invalid_transition'
