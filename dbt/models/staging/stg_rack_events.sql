{%- set columns = [
    'event_id', 'rack_id', 'site_code', 'vendor_code', 'rack_type_code', 'status', 'event_at',
    'event_at_raw', 'recorded_by', 'payload_version', '_ingest_date', '_run_id', '_loaded_at',
    '_source_file',
] -%}

with source as (

    select * from {{ source('raw', 'rack_events') }}

),

typed as (

    select
        event_id,
        upper(rack_id) as rack_id,
        upper(site_code) as site_code,
        upper(vendor_code) as vendor_code,
        -- payload v2 (schema drift) renamed rack_type_code to rack_model
        upper(
            case when payload_version = 2 then rack_model else rack_type_code end
        ) as rack_type_code,
        upper(status) as status,
        {{ parse_event_at('event_at') }} as event_at,
        event_at as event_at_raw,
        recorded_by,
        payload_version,
        _ingest_date,
        _run_id,
        _loaded_at,
        _source_file
    from source

),

deduped as (

    select
        {{ columns | join(',\n        ') }}
    from typed
    -- a re-emitted event keeps the copy loaded last
    qualify row_number() over (
        partition by event_id
        order by _loaded_at desc, _source_file desc
    ) = 1

)

select
    {{ columns | join(',\n    ') }}
from deduped
