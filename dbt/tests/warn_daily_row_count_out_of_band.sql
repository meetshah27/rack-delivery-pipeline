-- SPEC 6 (warn): daily raw row count within 50% to 200% of the trailing 7-day mean.
-- A missing day shows up as the next day's double volume.
{{ config(severity='warn') }}

with daily as (

    select
        _ingest_date,
        count(*) as rows_landed
    from {{ source('raw', 'rack_events') }}
    group by _ingest_date

),

with_trailing as (

    select
        _ingest_date,
        rows_landed,
        avg(rows_landed) over (
            order by _ingest_date
            rows between 7 preceding and 1 preceding
        ) as trailing_7d_mean,
        count(*) over (
            order by _ingest_date
            rows between 7 preceding and 1 preceding
        ) as trailing_days
    from daily

)

select
    _ingest_date,
    rows_landed,
    trailing_7d_mean
from with_trailing
where
    trailing_days = 7
    and (rows_landed < 0.5 * trailing_7d_mean or rows_landed > 2.0 * trailing_7d_mean)
