{#- YYYYMMDD integer key for dim_date from a date or timestamp expression. -#}
{% macro date_sk(expression) %}
    cast(
        extract(year from {{ expression }}) * 10000
        + extract(month from {{ expression }}) * 100
        + extract(day from {{ expression }})
        as {{ dbt.type_int() }}
    )
{% endmacro %}
