{#- Use the configured schema verbatim (staging, intermediate, marts, ops) instead of dbt's
    default <target_schema>_<custom_schema>, so both targets share one schema layout. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
