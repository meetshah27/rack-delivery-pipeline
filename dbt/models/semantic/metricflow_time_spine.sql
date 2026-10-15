-- Daily time spine for MetricFlow (metric_time), from the calendar dimension.
select cast(date as date) as date_day
from {{ ref('dim_date') }}
