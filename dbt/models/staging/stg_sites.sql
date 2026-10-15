with source as (

    select * from {{ source('raw', 'sites') }}

)

select
    upper(site_code) as site_code,
    site_name,
    site_region,
    country,
    cast(capacity_racks as {{ dbt.type_int() }}) as capacity_racks,
    cast(opened_date as date) as opened_date
from source
