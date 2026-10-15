{#- Transaction fact: one row per valid status change. -#}
{{
    config(
        materialized='incremental',
        incremental_strategy=incremental_strategy(),
        unique_key='event_sk',
        sort='event_at',
        dist='auto',
    )
}}

with
{%- if is_incremental() %}

    racks_to_build as (

        select distinct validated.rack_id
        from {{ ref('int_rack_events_validated') }} as validated
        where {{ ingest_window('validated._ingest_date', '_ingest_date') }}  -- noqa: LT05

    ),
{%- endif %}

events as (

    select
        event_id,
        rack_id,
        site_code,
        vendor_code,
        rack_type_code,
        status,
        prev_status,
        event_at,
        hours_since_prev_event,
        is_regression,
        late_by_days,
        _ingest_date,
        _run_id
    from {{ ref('int_rack_events_validated') }}
    {%- if is_incremental() %}
        where rack_id in (select racks_to_build.rack_id from racks_to_build)
    {%- endif %}

)

select
    {{ dbt_utils.generate_surrogate_key(['events.event_id']) }} as event_sk,
    events.event_id,
    events.rack_id,
    rack_types.rack_type_sk,
    sites.site_sk,
    vendors.vendor_sk,
    {{ date_sk('events.event_at') }} as event_date_sk,
    events.status,
    events.prev_status,
    events.event_at,
    events.hours_since_prev_event,
    events.is_regression,
    events.late_by_days,
    events._ingest_date,
    events._run_id
from events
left join {{ ref('dim_site') }} as sites
    on events.site_code = sites.site_code
left join {{ ref('dim_rack_type') }} as rack_types
    on events.rack_type_code = rack_types.rack_type_code
-- SCD2 as-of join: the contract version in effect when the event happened
left join {{ ref('dim_vendor') }} as vendors
    on
        events.vendor_code = vendors.vendor_code
        and events.event_at >= vendors.valid_from
        and events.event_at < vendors.valid_to
