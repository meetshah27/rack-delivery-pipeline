with source as (

    select * from {{ source('raw', 'vendor_contracts') }}

)

select
    upper(vendor_code) as vendor_code,
    vendor_name,
    cast(sla_days_order_to_production as {{ dbt.type_int() }}) as sla_days_order_to_production,
    lower(contract_tier) as contract_tier,
    region,
    _ingest_date
from source
