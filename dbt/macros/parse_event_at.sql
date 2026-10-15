{#- Source event_at arrives in three formats (timestamp format drift):
    ISO 8601 with Z, ISO 8601 with a UTC offset, and epoch milliseconds as a string.
    Returns a naive UTC timestamp. -#}
{% macro parse_event_at(column) %}
    {{ return(adapter.dispatch('parse_event_at')(column)) }}
{% endmacro %}

{% macro redshift__parse_event_at(column) %}
    case
        when {{ column }} ~ '^[0-9]+$'
            then timestamp 'epoch' + cast({{ column }} as bigint) / 1000 * interval '1 second'
        else cast({{ column }} as timestamptz) at time zone 'UTC'
    end
{% endmacro %}

{% macro duckdb__parse_event_at(column) %}
    case
        when regexp_full_match({{ column }}, '[0-9]+')
            then make_timestamp(cast({{ column }} as bigint) * 1000)
        else timezone('UTC', cast({{ column }} as timestamptz))
    end
{% endmacro %}
