-- Data quality (error): every event that reaches the validated layer follows an allowed transition,
-- directly or across a gap bridged by a future-dated event (see int_rack_events_checked).
select
    validated.event_id,
    validated.prev_status,
    validated.status
from {{ ref('int_rack_events_validated') }} as validated
left join {{ ref('status_transitions') }} as transitions
    on
        coalesce(validated.prev_status, 'START') = transitions.from_status
        and validated.status = transitions.to_status
where transitions.to_status is null and not validated.is_gap_bridged
