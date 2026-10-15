{#- Cycle time percentiles per month, vendor, site and stage. Each stage is anchored on the
    month its ending milestone was reached (vendor_lead on shipped_at, and so on). -#}
{%- set stages = [
    ('vendor_lead', 'vendor_lead_hours', 'shipped_at'),
    ('transit', 'transit_hours', 'received_at'),
    ('install', 'install_hours', 'cabled_at'),
    ('burn_in', 'burn_in_hours', 'production_at'),
    ('end_to_end', 'end_to_end_hours', 'production_at'),
] -%}

with stages as (

    {%- for stage, hours, ended_at in stages %}
        select
            vendor_sk,
            site_sk,
            '{{ stage }}' as stage,
            {{ ended_at }} as stage_ended_at,
            {{ hours }} as stage_hours
        from {{ ref('fct_rack_delivery') }}
        where {{ hours }} is not null
        {%- if not loop.last %}

            union all
        {%- endif %}
    {%- endfor %}

)

select
    cast({{ dbt.date_trunc('month', 'stages.stage_ended_at') }} as date) as month,
    vendors.vendor_code,
    sites.site_code,
    stages.stage,
    percentile_cont(0.5) within group (order by stages.stage_hours) as p50_hours,
    percentile_cont(0.9) within group (order by stages.stage_hours) as p90_hours,
    avg(stages.stage_hours) as mean_hours,
    count(*) as rack_count
from stages
inner join {{ ref('dim_vendor') }} as vendors
    on stages.vendor_sk = vendors.vendor_sk
inner join {{ ref('dim_site') }} as sites
    on stages.site_sk = sites.site_sk
group by 1, 2, 3, 4
