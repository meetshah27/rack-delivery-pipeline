{#- Duration in hours, DECIMAL(10,2) per the naming convention for <stage>_hours. -#}
{% macro hours_between(start_column, end_column) %}
    cast({{ dbt.datediff(start_column, end_column, 'second') }} / 3600.0 as decimal(10, 2))
{% endmacro %}
