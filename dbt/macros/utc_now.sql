{#- Current time as a naive UTC timestamp, comparable with the naive UTC *_at columns. -#}
{% macro utc_now() %}
    {{ return(adapter.dispatch('utc_now')()) }}
{% endmacro %}

{#- Redshift's getdate() is already naive UTC. -#}
{% macro default__utc_now() %}{{ dbt.current_timestamp() }}{% endmacro %}

{% macro duckdb__utc_now() %}timezone('UTC', now()){% endmacro %}
