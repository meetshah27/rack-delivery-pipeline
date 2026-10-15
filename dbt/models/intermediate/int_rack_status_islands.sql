{#-
    Gaps and islands: collapses consecutive events with the same status into one island
    (row_number difference technique) and flags flapping, i.e. a rack entering the same status
    more than once (a failed burn-in re-enters CABLED and BURN_IN).
-#}

with events as (

    select
        rack_id,
        status,
        event_at,
        event_id
    from {{ ref('int_rack_events_validated') }}

),

numbered as (

    select
        rack_id,
        status,
        event_at,
        row_number() over (partition by rack_id order by event_at, event_id)
        - row_number() over (partition by rack_id, status order by event_at, event_id)
            as island_key
    from events

),

islands as (

    select
        rack_id,
        status,
        island_key,
        min(event_at) as island_started_at,
        max(event_at) as island_ended_at,
        count(*) as event_count
    from numbered
    group by rack_id, status, island_key

)

select
    rack_id,
    status,
    island_started_at,
    island_ended_at,
    event_count,
    row_number() over (
        partition by rack_id, status
        order by island_started_at
    ) as status_entry_number,
    count(*) over (partition by rack_id, status) > 1 as is_flapping
from islands
