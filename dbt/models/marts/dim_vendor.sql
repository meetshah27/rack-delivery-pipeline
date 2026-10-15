{#- SCD2: one row per contract version. Facts join as of a date:
    valid_from <= date < valid_to. -#}

with versions as (

    select
        vendor_code,
        vendor_name,
        sla_days_order_to_production,
        contract_tier,
        dbt_valid_from,
        dbt_valid_to
    from {{ ref('snap_vendor_contracts') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['vendor_code', 'dbt_valid_from']) }} as vendor_sk,
    vendor_code,
    vendor_name,
    sla_days_order_to_production,
    contract_tier,
    dbt_valid_from as valid_from,
    dbt_valid_to as valid_to,
    dbt_valid_to = cast('9999-12-31' as {{ dbt.type_timestamp() }}) as is_current
from versions
