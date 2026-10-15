{#- Facts merge on Redshift; DuckDB uses delete+insert. Both replace rows by unique_key. -#}
{% macro incremental_strategy() %}
    {{ return(adapter.dispatch('incremental_strategy')()) }}
{% endmacro %}

{% macro default__incremental_strategy() %}{{ return('merge') }}{% endmacro %}

{% macro duckdb__incremental_strategy() %}{{ return('delete+insert') }}{% endmacro %}
