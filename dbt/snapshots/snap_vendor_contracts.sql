{#-
    SCD2 history of vendor contracts. Captures the contract state landed on `contracts_as_of`
    (default: the latest ingest date). updated_at makes dbt_valid_from the contract date, not
    the run time, so history replayed by ops/snapshot_replay.py carries the right dates.
-#}
{% snapshot snap_vendor_contracts %}

{{
    config(
        target_schema='intermediate',
        unique_key='vendor_code',
        strategy='check',
        check_cols=['sla_days_order_to_production', 'contract_tier'],
        updated_at='contract_as_of',
        dbt_valid_to_current="cast('9999-12-31' as timestamp)",
    )
}}

with contracts as (

    select
        vendor_code,
        vendor_name,
        sla_days_order_to_production,
        contract_tier,
        region,
        _ingest_date
    from {{ ref('stg_vendor_contracts') }}

)

select
    vendor_code,
    vendor_name,
    sla_days_order_to_production,
    contract_tier,
    region,
    cast(_ingest_date as {{ dbt.type_timestamp() }}) as contract_as_of
from contracts
where _ingest_date = {% if var('contracts_as_of') is not none -%}
    cast('{{ var("contracts_as_of") }}' as date)
{%- else -%}
    (select max(_ingest_date) from contracts)
{%- endif %}

{% endsnapshot %}
