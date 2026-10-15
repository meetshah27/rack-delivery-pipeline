{#-
    Classifies every staged event. Rows with a null quarantine_reason continue to
    int_rack_events_validated; the rest go to ops.quarantine_rack_events.

    Transitions are checked in two passes. A spurious event (one that jumps ahead, e.g.
    RECEIVED -> PRODUCTION) also makes the real event after it look invalid when judged against
    its immediate predecessor. Pass 1 marks an event spurious when it does not follow from its
    predecessor, the predecessor connects straight to its successor without it, and the
    predecessor itself follows from the event before it (otherwise the predecessor is the
    suspect, not this event). Pass 2 re-checks everything else against the previous
    non-spurious event.

    A one-step gap is bridged when the missing status did arrive but was quarantined as
    future-dated: the rack's history is complete, only that event's timestamp is broken.
    Gaps from events that have not arrived yet stay invalid until the late event lands and
    the next build re-validates the rack.
-#}
{%- set columns = [
    'event_id', 'rack_id', 'site_code', 'vendor_code', 'rack_type_code', 'status', 'event_at',
    'recorded_by', '_ingest_date', '_run_id', '_loaded_at',
] -%}
{%- set rack_order = 'partition by rack_id order by event_at, event_id' -%}

with events as (

    select
        {{ columns | join(',\n        ') }}
    from {{ ref('stg_rack_events') }}

),

transitions as (

    select
        from_status,
        to_status,
        is_regression
    from {{ ref('status_transitions') }}

),

dated as (

    select
        {{ columns | join(',\n        ') }},
        -- judged against load time, not wall-clock now: the row was future-dated when it
        -- arrived, and the verdict stays the same on every rebuild (idempotent backfills)
        event_at > {{ dbt.dateadd('hour', 1, '_loaded_at') }} as is_future_dated
    from events

),

neighbours as (

    select
        {{ columns | join(',\n        ') }},
        coalesce(lag(status, 2) over ({{ rack_order }}), 'START') as pass1_prev2_status,
        coalesce(lag(status) over ({{ rack_order }}), 'START') as pass1_prev_status,
        lead(status) over ({{ rack_order }}) as pass1_next_status
    from dated
    where not is_future_dated

),

pass1 as (

    select
        neighbours.{{ columns | join(',
        neighbours.') }},
        neighbours.pass1_prev_status,
        (
            transition_in.to_status is null
            and transition_skip.to_status is not null
            and (neighbours.pass1_prev_status = 'START' or transition_prev.to_status is not null)
        ) as is_spurious
    from neighbours
    left join transitions as transition_in
        on
            neighbours.pass1_prev_status = transition_in.from_status
            and neighbours.status = transition_in.to_status
    left join transitions as transition_skip
        on
            neighbours.pass1_prev_status = transition_skip.from_status
            and neighbours.pass1_next_status = transition_skip.to_status
    left join transitions as transition_prev
        on
            neighbours.pass1_prev2_status = transition_prev.from_status
            and neighbours.pass1_prev_status = transition_prev.to_status

),

pass2 as (

    select
        {{ columns | join(',\n        ') }},
        coalesce(lag(status) over ({{ rack_order }}), 'START') as prev_status,
        lag(event_at) over ({{ rack_order }}) as prev_event_at
    from pass1
    where not is_spurious

),

future_statuses as (

    select distinct
        rack_id,
        status
    from dated
    where is_future_dated

),

bridged as (

    select pass2.event_id
    from pass2
    inner join future_statuses
        on pass2.rack_id = future_statuses.rack_id
    inner join transitions as into_gap
        on
            pass2.prev_status = into_gap.from_status
            and future_statuses.status = into_gap.to_status
    inner join transitions as out_of_gap
        on
            future_statuses.status = out_of_gap.from_status
            and pass2.status = out_of_gap.to_status
    group by pass2.event_id

),

checked as (

    select
        pass2.{{ columns | join(',
        pass2.') }},
        pass2.prev_status,
        pass2.prev_event_at,
        (
            transitions.to_status is not null or bridged.event_id is not null
        ) as is_valid_transition,
        coalesce(transitions.is_regression, false) as is_regression,
        (transitions.to_status is null and bridged.event_id is not null) as is_gap_bridged,
        false as is_future_dated,
        case
            when transitions.to_status is null and bridged.event_id is null
                then 'invalid_transition'
        end as quarantine_reason
    from pass2
    left join transitions
        on
            pass2.prev_status = transitions.from_status
            and pass2.status = transitions.to_status
    left join bridged
        on pass2.event_id = bridged.event_id

),

spurious as (

    select
        {{ columns | join(',\n        ') }},
        pass1_prev_status as prev_status,
        cast(null as {{ dbt.type_timestamp() }}) as prev_event_at,
        false as is_valid_transition,
        false as is_regression,
        false as is_gap_bridged,
        false as is_future_dated,
        'invalid_transition' as quarantine_reason
    from pass1
    where is_spurious

),

future_dated as (

    select
        {{ columns | join(',\n        ') }},
        cast(null as {{ dbt.type_string() }}) as prev_status,
        cast(null as {{ dbt.type_timestamp() }}) as prev_event_at,
        false as is_valid_transition,
        false as is_regression,
        false as is_gap_bridged,
        true as is_future_dated,
        'future_dated' as quarantine_reason
    from dated
    where is_future_dated

),

unioned as (

    select
        {{ columns | join(',
        ') }},
        prev_status,
        prev_event_at,
        is_valid_transition,
        is_regression,
        is_gap_bridged,
        is_future_dated,
        quarantine_reason
    from checked

    union all

    select
        {{ columns | join(',
        ') }},
        prev_status,
        prev_event_at,
        is_valid_transition,
        is_regression,
        is_gap_bridged,
        is_future_dated,
        quarantine_reason
    from spurious

    union all

    select
        {{ columns | join(',
        ') }},
        prev_status,
        prev_event_at,
        is_valid_transition,
        is_regression,
        is_gap_bridged,
        is_future_dated,
        quarantine_reason
    from future_dated

)

select
    {{ columns | join(',\n    ') }},
    nullif(prev_status, 'START') as prev_status,
    prev_event_at,
    is_valid_transition,
    is_regression,
    is_gap_bridged,
    is_future_dated,
    {{ dbt.datediff('cast(event_at as date)', '_ingest_date', 'day') }} as late_by_days,  -- noqa: LT05
    quarantine_reason
from unioned
