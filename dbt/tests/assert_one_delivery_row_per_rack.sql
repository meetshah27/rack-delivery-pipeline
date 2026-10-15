-- Data quality (error): each rack with a valid event has exactly one row in fct_rack_delivery.
with expected as (

    select distinct rack_id
    from {{ ref('int_rack_events_validated') }}

)

select expected.rack_id
from expected
left join {{ ref('fct_rack_delivery') }} as deliveries
    on expected.rack_id = deliveries.rack_id
where deliveries.rack_id is null
