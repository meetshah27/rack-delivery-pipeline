-- p50 and p90 cycle time per stage and vendor in days, for racks that reached production.
-- Answers "which stage drives p90?" across the whole history.
with completed as (

    select
        vendors.vendor_code,
        deliveries.vendor_lead_hours,
        deliveries.transit_hours,
        deliveries.install_hours,
        deliveries.burn_in_hours,
        deliveries.end_to_end_hours
    from {{ ref('fct_rack_delivery') }} as deliveries
    inner join {{ ref('dim_vendor') }} as vendors
        on deliveries.vendor_sk = vendors.vendor_sk
    where deliveries.production_at is not null

)

select
    vendor_code,
    percentile_cont(0.5) within group (order by end_to_end_hours) / 24 as p50_end_to_end_days,
    percentile_cont(0.9) within group (order by end_to_end_hours) / 24 as p90_end_to_end_days,
    avg(vendor_lead_hours) / 24 as mean_vendor_lead_days,
    avg(transit_hours) / 24 as mean_transit_days,
    avg(install_hours) / 24 as mean_install_days,
    avg(burn_in_hours) / 24 as mean_burn_in_days,
    count(*) as racks
from completed
group by vendor_code
order by p90_end_to_end_days desc
