\set ON_ERROR_STOP on
\if :{?row_count}
\else
\set row_count 5000000
\endif
\if :{?customer_count}
\else
\set customer_count 100000
\endif

\timing on

DROP SCHEMA IF EXISTS datepipette_benchmark CASCADE;
CREATE SCHEMA datepipette_benchmark;

CREATE TABLE datepipette_benchmark.customers (
    customer_id bigint PRIMARY KEY,
    customer_name text NOT NULL,
    segment text NOT NULL,
    home_region text NOT NULL,
    signup_date date NOT NULL
);

INSERT INTO datepipette_benchmark.customers (
    customer_id,
    customer_name,
    segment,
    home_region,
    signup_date
)
SELECT
    value,
    'Customer ' || value,
    'segment-' || (value % 8),
    'region-' || (value % 12),
    DATE '2020-01-01' + ((value - 1) % 1461)::integer
FROM generate_series(1, :customer_count) AS series(value);

CREATE TABLE datepipette_benchmark.events (
    event_id bigint NOT NULL,
    customer_id bigint NOT NULL,
    occurred_at timestamptz NOT NULL,
    event_date date NOT NULL,
    category text NOT NULL,
    status text NOT NULL,
    region text NOT NULL,
    channel text NOT NULL,
    device_type text NOT NULL,
    product_code text NOT NULL,
    campaign_code text NOT NULL,
    country_code text NOT NULL,
    currency_code text NOT NULL,
    amount numeric(14, 2) NOT NULL,
    tax_amount numeric(12, 2) NOT NULL,
    discount_amount numeric(12, 2) NOT NULL,
    unit_price numeric(12, 2) NOT NULL,
    quantity integer NOT NULL,
    score double precision NOT NULL,
    latency_ms integer NOT NULL,
    duration_seconds integer NOT NULL,
    retry_count smallint NOT NULL,
    page_count smallint NOT NULL,
    item_count integer NOT NULL,
    priority smallint NOT NULL,
    risk_score double precision NOT NULL,
    metric_01 bigint NOT NULL,
    metric_02 bigint NOT NULL,
    metric_03 integer NOT NULL,
    metric_04 integer NOT NULL,
    metric_05 integer NOT NULL,
    metric_06 integer NOT NULL,
    metric_07 integer NOT NULL,
    metric_08 integer NOT NULL,
    metric_09 integer NOT NULL,
    metric_10 integer NOT NULL,
    flag_01 boolean NOT NULL,
    flag_02 boolean NOT NULL,
    flag_03 boolean NOT NULL,
    flag_04 boolean NOT NULL,
    flag_05 boolean NOT NULL,
    attribute_01 text NOT NULL,
    attribute_02 text NOT NULL,
    attribute_03 text NOT NULL,
    attribute_04 text NOT NULL,
    attribute_05 text NOT NULL,
    attribute_06 text NOT NULL,
    attribute_07 text NOT NULL,
    attribute_08 text NOT NULL,
    description text NOT NULL
);

SET synchronous_commit = off;

INSERT INTO datepipette_benchmark.events
SELECT
    value AS event_id,
    ((value - 1) % :customer_count) + 1 AS customer_id,
    TIMESTAMPTZ '2024-01-01 00:00:00+00'
        + ((value - 1) % 63072000) * INTERVAL '1 second' AS occurred_at,
    DATE '2024-01-01' + ((value - 1) % 730)::integer AS event_date,
    'category-' || (value % 20),
    'status-' || (value % 6),
    'region-' || (value % 12),
    'channel-' || (value % 8),
    'device-' || (value % 5),
    'product-' || (value % 1000),
    'campaign-' || (value % 100),
    'country-' || (value % 30),
    CASE value % 4 WHEN 0 THEN 'USD' WHEN 1 THEN 'EUR' WHEN 2 THEN 'GBP' ELSE 'KZT' END,
    ((value % 100000) / 100.0)::numeric(14, 2),
    ((value % 20000) / 100.0)::numeric(12, 2),
    ((value % 5000) / 100.0)::numeric(12, 2),
    (((value % 25000) + 100) / 100.0)::numeric(12, 2),
    ((value % 20) + 1)::integer,
    ((value % 10000) / 100.0)::double precision,
    ((value * 17) % 5000)::integer,
    ((value * 7) % 86400)::integer,
    (value % 5)::smallint,
    ((value % 25) + 1)::smallint,
    ((value % 50) + 1)::integer,
    (value % 5)::smallint,
    ((value * 13) % 10000 / 100.0)::double precision,
    value * 3,
    value * 7,
    ((value * 11) % 1000000)::integer,
    ((value * 13) % 1000000)::integer,
    ((value * 17) % 1000000)::integer,
    ((value * 19) % 1000000)::integer,
    ((value * 23) % 1000000)::integer,
    ((value * 29) % 1000000)::integer,
    ((value * 31) % 1000000)::integer,
    ((value * 37) % 1000000)::integer,
    value % 2 = 0,
    value % 3 = 0,
    value % 5 = 0,
    value % 7 = 0,
    value % 11 = 0,
    'attribute-a-' || (value % 10),
    'attribute-b-' || (value % 20),
    'attribute-c-' || (value % 30),
    'attribute-d-' || (value % 40),
    'attribute-e-' || (value % 50),
    'attribute-f-' || (value % 60),
    'attribute-g-' || (value % 70),
    'attribute-h-' || (value % 80),
    'Synthetic benchmark event class ' || (value % 100)
FROM generate_series(1, :row_count) AS series(value);

ALTER TABLE datepipette_benchmark.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id);
ALTER TABLE datepipette_benchmark.events
    ADD CONSTRAINT events_customer_fk
    FOREIGN KEY (customer_id)
    REFERENCES datepipette_benchmark.customers(customer_id)
    NOT VALID;

CREATE INDEX events_customer_id_idx ON datepipette_benchmark.events(customer_id);
CREATE INDEX events_occurred_at_idx ON datepipette_benchmark.events(occurred_at);
CREATE INDEX events_category_idx ON datepipette_benchmark.events(category);

ANALYZE datepipette_benchmark.customers;
ANALYZE datepipette_benchmark.events;

SELECT
    count(*) AS event_rows,
    count(*) FILTER (WHERE event_id IS NULL) AS invalid_rows
FROM datepipette_benchmark.events;

SELECT
    pg_size_pretty(pg_total_relation_size('datepipette_benchmark.events')) AS events_total,
    pg_size_pretty(pg_relation_size('datepipette_benchmark.events')) AS events_heap,
    pg_size_pretty(pg_indexes_size('datepipette_benchmark.events')) AS events_indexes,
    pg_size_pretty(pg_total_relation_size('datepipette_benchmark.customers')) AS customers_total;
