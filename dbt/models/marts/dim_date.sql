{%- set fiscal_start = var('fiscal_year_start_month') -%}

with spine as (

    {{ dbt_utils.date_spine(
        datepart='day',
        start_date="cast('2023-01-01' as date)",
        end_date="cast('2029-01-01' as date)",
    ) }}

),

days as (

    select
        cast(date_day as date) as date_day,
        cast(extract(year from date_day) as {{ dbt.type_int() }}) as calendar_year,
        cast(extract(month from date_day) as {{ dbt.type_int() }}) as calendar_month,
        cast(extract(dow from date_day) as {{ dbt.type_int() }}) as day_of_week
    from spine

)

select
    {{ date_sk('date_day') }} as date_sk,
    date_day as date,
    calendar_year as year,
    cast(extract(quarter from date_day) as {{ dbt.type_int() }}) as quarter,
    calendar_month as month,
    case calendar_month
        when 1 then 'January'
        when 2 then 'February'
        when 3 then 'March'
        when 4 then 'April'
        when 5 then 'May'
        when 6 then 'June'
        when 7 then 'July'
        when 8 then 'August'
        when 9 then 'September'
        when 10 then 'October'
        when 11 then 'November'
        when 12 then 'December'
    end as month_name,
    cast(extract(week from date_day) as {{ dbt.type_int() }}) as iso_week,
    day_of_week,
    day_of_week in (0, 6) as is_weekend,
    -- fiscal year is named for the calendar year it ends in
    calendar_year
    + case
        when {{ fiscal_start }} > 1 and calendar_month >= {{ fiscal_start }} then 1 else 0
    end as fiscal_year,
    cast(
        floor(mod(calendar_month - {{ fiscal_start }} + 12, 12) / 3) + 1 as {{ dbt.type_int() }}
    ) as fiscal_quarter
from days
