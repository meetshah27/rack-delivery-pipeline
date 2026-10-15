-- Cohort analysis: for each order-month cohort, the share of racks in production by N weeks
-- after their order date.
{%- set weeks = [4, 6, 8, 10, 12] %}

with racks as (

    select
        cast({{ dbt.date_trunc('month', 'ordered_at') }} as date) as order_month,
        {{ dbt.datediff('ordered_at', 'production_at', 'day') }} as days_to_production  -- noqa: LT05
    from {{ ref('fct_rack_delivery') }}
    where ordered_at is not null

)

select
    order_month,
    {%- for week in weeks %}
        cast(sum(case when days_to_production <= {{ week * 7 }} then 1 else 0 end) as float)
        / count(*) as pct_in_production_by_week_{{ week }},
    {%- endfor %}
    count(*) as cohort_racks
from racks
group by order_month
order by order_month
