{#- First 8 hex digits of md5(expression) as an integer. Summed over rows it gives an
    order-independent checksum of a table's key columns (no overflow up to ~2 billion rows). -#}
{% macro md5_bigint(expression) %}
    {{ return(adapter.dispatch('md5_bigint')(expression)) }}
{% endmacro %}

{% macro redshift__md5_bigint(expression) %}
    strtol(substring(md5({{ expression }}), 1, 8), 16)
{% endmacro %}

{% macro duckdb__md5_bigint(expression) %}
    cast('0x' || substring(md5({{ expression }}), 1, 8) as bigint)
{% endmacro %}
