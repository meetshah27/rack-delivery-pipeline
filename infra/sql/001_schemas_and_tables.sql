-- Schemas and the tables written outside dbt: raw landing tables (COPY targets), the file
-- manifest, the run audit, and the simulator ground truth (dev only). Portable SQL: the
-- orchestration tests run this same file on DuckDB.
--
-- raw.* column order matches the Parquet written by orchestration/land_to_raw.py, because
-- COPY ... FORMAT AS PARQUET maps columns by position.

create schema if not exists raw;
create schema if not exists staging;
create schema if not exists intermediate;
create schema if not exists marts;
create schema if not exists ops;
create schema if not exists ground_truth;

create table if not exists raw.rack_events (
    event_id varchar(36),
    rack_id varchar(20),
    site_code varchar(10),
    vendor_code varchar(10),
    rack_type_code varchar(20),
    rack_model varchar(20),
    status varchar(30),
    event_at varchar(40),
    recorded_by varchar(50),
    payload_version smallint,
    _ingest_date date,
    _run_id varchar(64),
    _loaded_at timestamp,
    _source_file varchar(512)
);

create table if not exists raw.vendor_contracts (
    vendor_code varchar(10),
    vendor_name varchar(100),
    sla_days_order_to_production varchar(10),
    contract_tier varchar(20),
    region varchar(20),
    _ingest_date date
);

create table if not exists raw.sites (
    site_code varchar(10),
    site_name varchar(100),
    site_region varchar(20),
    country varchar(10),
    capacity_racks varchar(10),
    opened_date varchar(10)
);

create table if not exists raw.rack_types (
    rack_type_code varchar(20),
    description varchar(100),
    power_kw varchar(10),
    is_gpu varchar(5)
);

create table if not exists ops.file_manifest (
    source_file varchar(512),
    md5 varchar(32),
    row_count bigint,
    _ingest_date date,
    run_id varchar(64),
    loaded_at timestamp
);

create table if not exists ops.pipeline_run_audit (
    run_id varchar(64),
    started_at timestamp,
    finished_at timestamp,
    status varchar(16),
    ingest_date_min date,
    ingest_date_max date,
    rows_landed bigint,
    rows_loaded_raw bigint,
    rows_quarantined bigint,
    rows_fct_event_merged bigint,
    late_event_count bigint,
    dbt_tests_passed integer,
    dbt_tests_warned integer,
    dbt_tests_failed integer,
    redshift_charged_seconds bigint,
    est_cost_usd decimal(12, 4),
    error varchar(1024)
);

create table if not exists ground_truth.racks (
    rack_id varchar(20),
    site_code varchar(10),
    vendor_code varchar(10),
    rack_type_code varchar(20),
    ordered_at timestamp,
    vendor_confirmed_at timestamp,
    shipped_at timestamp,
    received_at timestamp,
    racked_at timestamp,
    cabled_at timestamp,
    burn_in_at timestamp,
    production_at timestamp,
    decommission_requested_at timestamp,
    decommissioned_at timestamp,
    regression_count integer
);
