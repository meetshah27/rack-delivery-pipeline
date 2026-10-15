{#- Racks reaching production per site per day, a 7-day moving average, and open work in
    progress (running orders minus running completions). Every site has a row every day, so
    the moving average counts quiet days as zero. -#}

with racks as (

    select
        site_sk,
        cast(ordered_at as date) as ordered_date,
        cast(production_at as date) as production_date
    from {{ ref('fct_rack_delivery') }}
    where ordered_at is not null

),

bounds as (

    select
        min(ordered_date) as first_date,
        max(coalesce(production_date, ordered_date)) as last_date
    from racks

),

grid as (

    select
        dates.date,
        sites.site_sk,
        sites.site_code
    from {{ ref('dim_date') }} as dates
    inner join bounds
        on dates.date between bounds.first_date and bounds.last_date
    cross join {{ ref('dim_site') }} as sites

),

ordered as (

    select
        site_sk,
        ordered_date as date,
        count(*) as racks_ordered
    from racks
    group by 1, 2

),

completed as (

    select
        site_sk,
        production_date as date,
        count(*) as racks_to_production
    from racks
    where production_date is not null
    group by 1, 2

),

daily as (

    select
        grid.date,
        grid.site_code,
        coalesce(ordered.racks_ordered, 0) as racks_ordered,
        coalesce(completed.racks_to_production, 0) as racks_to_production
    from grid
    left join ordered
        on grid.site_sk = ordered.site_sk and grid.date = ordered.date
    left join completed
        on grid.site_sk = completed.site_sk and grid.date = completed.date

)

select
    date,
    site_code,
    racks_to_production,
    avg(racks_to_production) over (
        partition by site_code
        order by date
        rows between 6 preceding and current row
    ) as racks_to_production_7d_avg,
    sum(racks_ordered)
        over (
            partition by site_code
            order by date
            rows between unbounded preceding and current row
        )
    - sum(racks_to_production) over (
        partition by site_code
        order by date
        rows between unbounded preceding and current row
    ) as open_wip
from daily
