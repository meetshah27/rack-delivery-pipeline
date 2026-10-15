select
    {{ dbt_utils.generate_surrogate_key(['rack_type_code']) }} as rack_type_sk,
    rack_type_code,
    description,
    power_kw,
    is_gpu
from {{ ref('stg_rack_types') }}
