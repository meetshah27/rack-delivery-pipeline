-- The pipeline recovers the truth: at least 99% of the simulator's racks have exactly the
-- true production_at in fct_rack_delivery (both null counts as a match).
-- Rate printed by: python -m ops.ground_truth
with compared as (

    select
        case
            when
                deliveries.production_at = truth.production_at
                or (deliveries.production_at is null and truth.production_at is null)
                then 1
            else 0
        end as is_match
    from {{ source('ground_truth', 'racks') }} as truth
    left join {{ ref('fct_rack_delivery') }} as deliveries
        on truth.rack_id = deliveries.rack_id

)

select
    count(*) as total_racks,
    sum(is_match) as matched_racks
from compared
having sum(is_match) < 0.99 * count(*)
