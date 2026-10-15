{#- On-time delivery per vendor per production month. Rates are pooled (on_time / completed),
    so they add up correctly across months and vendors. -#}

with completed as (

    select
        cast({{ dbt.date_trunc('month', 'deliveries.production_at') }} as date) as month,
        vendors.vendor_code,
        deliveries.is_sla_breached
    from {{ ref('fct_rack_delivery') }} as deliveries
    inner join {{ ref('dim_vendor') }} as vendors
        on deliveries.vendor_sk = vendors.vendor_sk
    where deliveries.production_at is not null

),

monthly as (

    select
        month,
        vendor_code,
        count(*) as racks_completed,
        sum(case when not is_sla_breached then 1 else 0 end) as on_time_racks
    from completed
    group by 1, 2

)

select
    month,
    vendor_code,
    racks_completed,
    cast(on_time_racks as bigint) as on_time,
    cast(on_time_racks as double precision) / racks_completed as on_time_pct
from monthly
