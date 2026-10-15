{#-
    Accumulating snapshot fact: one row per rack, updated in place as milestones are reached.
    Each milestone is the first time the status was reached; regressions do not reset it.

    Incremental runs recompute whole racks: those with events in the lookback (or backfill)
    window, plus every rack still open, whose days_in_current_status and is_overdue_open move
    with the clock. For racks already in production, days_in_current_status is as of the
    rack's last recompute.
-#}
{{
    config(
        materialized='incremental',
        incremental_strategy=incremental_strategy(),
        unique_key='rack_sk',
        sort='ordered_at',
        dist='auto',
    )
}}
{%- set milestones = {
    'ORDERED': 'ordered_at',
    'VENDOR_CONFIRMED': 'vendor_confirmed_at',
    'SHIPPED': 'shipped_at',
    'RECEIVED': 'received_at',
    'RACKED': 'racked_at',
    'CABLED': 'cabled_at',
    'BURN_IN': 'burn_in_started_at',
    'PRODUCTION': 'production_at',
    'DECOMMISSION_REQUESTED': 'decommission_requested_at',
    'DECOMMISSIONED': 'decommissioned_at',
} -%}

with
{%- if is_incremental() %}

    racks_to_build as (

        select validated.rack_id
        from {{ ref('int_rack_events_validated') }} as validated
        where {{ ingest_window('validated._ingest_date', '_last_ingest_date') }}  -- noqa: LT05

        union

        select open_racks.rack_id
        from {{ this }} as open_racks
        where open_racks.production_at is null and open_racks.decommissioned_at is null

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
        event_at,
        is_regression,
        _ingest_date
    from {{ ref('int_rack_events_validated') }}
    {%- if is_incremental() %}
        where rack_id in (select racks_to_build.rack_id from racks_to_build)
    {%- endif %}

),

-- conditional aggregation pivot: one column per milestone, first time reached
milestones as (

    select
        rack_id,
        {%- for status, column in milestones.items() %}
            min(case when status = '{{ status }}' then event_at end) as {{ column }},
        {%- endfor %}
        sum(case when is_regression then 1 else 0 end) as regression_count,
        max(event_at) as last_event_at,
        max(_ingest_date) as _last_ingest_date
    from events
    group by rack_id

),

latest as (

    select
        rack_id,
        site_code,
        vendor_code,
        rack_type_code,
        status as current_status,
        event_at as current_status_since
    from events
    qualify row_number() over (
        partition by rack_id
        order by event_at desc, event_id desc
    ) = 1

),

racks as (

    select
        milestones.rack_id,
        latest.site_code,
        latest.vendor_code,
        latest.rack_type_code,
        milestones.{{ milestones.values() | join(',\n        milestones.') }},
        latest.current_status,
        latest.current_status_since,
        milestones.regression_count,
        milestones.last_event_at,
        milestones._last_ingest_date
    from milestones
    inner join latest
        on milestones.rack_id = latest.rack_id

),

with_sla as (

    select
        racks.rack_id,
        sites.site_sk,
        vendors.vendor_sk,
        rack_types.rack_type_sk,
        vendors.sla_days_order_to_production as sla_days,
        {{ dbt.dateadd('day', 'vendors.sla_days_order_to_production', 'racks.ordered_at') }}
            as sla_due_at,
        racks.{{ milestones.values() | join(',\n        racks.') }},
        racks.current_status,
        racks.current_status_since,
        racks.regression_count,
        racks.last_event_at,
        racks._last_ingest_date
    from racks
    left join {{ ref('dim_site') }} as sites
        on racks.site_code = sites.site_code
    left join {{ ref('dim_rack_type') }} as rack_types
        on racks.rack_type_code = rack_types.rack_type_code
    -- SCD2 as-of join: a rack is judged against the SLA in effect when it was ordered
    left join {{ ref('dim_vendor') }} as vendors
        on
            racks.vendor_code = vendors.vendor_code
            and racks.ordered_at >= vendors.valid_from
            and racks.ordered_at < vendors.valid_to

)

select
    {{ dbt_utils.generate_surrogate_key(['rack_id']) }} as rack_sk,
    rack_id,
    site_sk,
    vendor_sk,
    rack_type_sk,
    {{ date_sk('ordered_at') }} as ordered_date_sk,
    {{ date_sk('production_at') }} as production_date_sk,
    {{ milestones.values() | join(',\n    ') }},
    {{ hours_between('ordered_at', 'shipped_at') }} as vendor_lead_hours,  -- noqa: LT05
    {{ hours_between('shipped_at', 'received_at') }} as transit_hours,  -- noqa: LT05
    {{ hours_between('received_at', 'cabled_at') }} as install_hours,  -- noqa: LT05
    {{ hours_between('cabled_at', 'production_at') }} as burn_in_hours,  -- noqa: LT05
    {{ hours_between('ordered_at', 'production_at') }} as end_to_end_hours,  -- noqa: LT05
    current_status,
    current_status_since,
    {{ dbt.datediff('current_status_since', utc_now(), 'day') }} as days_in_current_status,  -- noqa: LT05
    sla_days,
    sla_due_at,
    coalesce(production_at > sla_due_at, false) as is_sla_breached,
    coalesce(
        production_at is null
        and decommissioned_at is null
        and {{ utc_now() }} > sla_due_at,
        false
    ) as is_overdue_open,
    regression_count,
    last_event_at,
    '{{ var("run_id") }}' as _updated_run_id,
    _last_ingest_date
from with_sla
