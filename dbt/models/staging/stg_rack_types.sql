with source as (

    select * from {{ source('raw', 'rack_types') }}

)

select
    upper(rack_type_code) as rack_type_code,
    description,
    cast(power_kw as {{ dbt.type_int() }}) as power_kw,
    cast(is_gpu as {{ dbt.type_boolean() }}) as is_gpu
from source
