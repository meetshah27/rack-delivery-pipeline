{#-
    QuickSight row-level security rules for ds_delivery: which QuickSight user sees which
    site_region. Readers come from the rls_users seed; the dashboard owner (var
    qs_owner_user_name) gets a null region, which QuickSight treats as "all regions".
    The QuickSight dataset renames user_name to the UserName column RLS expects.
-#}

with readers as (

    select
        user_name,
        site_region
    from {{ ref('rls_users') }}

)

select
    user_name,
    site_region
from readers
{%- if var('qs_owner_user_name') is not none %}

    union all

    select
        '{{ var("qs_owner_user_name") }}' as user_name,
        cast(null as varchar) as site_region
{%- endif %}
