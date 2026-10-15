select
    {{ dbt_utils.generate_surrogate_key(['site_code']) }} as site_sk,
    site_code,
    site_name,
    site_region,
    country,
    capacity_racks,
    opened_date
from {{ ref('stg_sites') }}
