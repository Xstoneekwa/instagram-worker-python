\set ON_ERROR_STOP on
truncate follow60_ordering_v2_test.outbox, follow60_ordering_v2_test.receipts, follow60_ordering_v2_test.ledger restart identity cascade;
create temporary table follow60_ordering_v2_bench(kind text, duration_ms double precision);

do $$
declare
    i integer;
    started timestamptz;
    ledger bigint;
    receipt bigint;
begin
    for i in 1..500 loop
        started := clock_timestamp();
        perform follow60_ordering_v2_test.apply_receipt(
            'bench-account', 'bench-run', 'bench-request', 'bench-session', 'bench-target',
            'insert-' || i, 'candidate-' || i, 'FOLLOW60_ORDERING_V2_LEDGER_V1',
            'profile_certified', '{"exact":true}'::jsonb
        );
        insert into follow60_ordering_v2_bench values (
            'insert_update_outbox', extract(epoch from clock_timestamp() - started) * 1000.0
        );
    end loop;

    for i in 1..500 loop
        perform follow60_ordering_v2_test.apply_receipt(
            'bench-account', 'bench-run', 'bench-request', 'bench-session', 'bench-target',
            'like-' || i, 'candidate-' || i, 'FOLLOW60_ORDERING_V2_LEDGER_V1',
            'profile_certified', '{"exact":true}'::jsonb
        );
        perform follow60_ordering_v2_test.apply_receipt(
            'bench-account', 'bench-run', 'bench-request', 'bench-session', 'bench-target',
            'like-' || i, 'candidate-' || i, 'FOLLOW60_ORDERING_V2_LEDGER_V1',
            'post_opened', '{"v5":true}'::jsonb
        );
        started := clock_timestamp();
        perform follow60_ordering_v2_test.apply_receipt(
            'bench-account', 'bench-run', 'bench-request', 'bench-session', 'bench-target',
            'like-' || i, 'candidate-' || i, 'FOLLOW60_ORDERING_V2_LEDGER_V1',
            'like_verified', '{"verified":true}'::jsonb
        );
        insert into follow60_ordering_v2_bench values (
            'like_receipt_rpc', extract(epoch from clock_timestamp() - started) * 1000.0
        );

        started := clock_timestamp();
        perform follow60_ordering_v2_test.apply_receipt(
            'bench-account', 'bench-run', 'bench-request', 'bench-session', 'bench-target',
            'like-' || i, 'candidate-' || i, 'FOLLOW60_ORDERING_V2_LEDGER_V1',
            'like_verified', '{"verified":true}'::jsonb
        );
        insert into follow60_ordering_v2_bench values (
            'duplicate_replay', extract(epoch from clock_timestamp() - started) * 1000.0
        );
    end loop;

    for receipt in
        select r.receipt_id
        from follow60_ordering_v2_test.receipts r
        join follow60_ordering_v2_test.outbox o using (receipt_id)
        where r.action_type = 'like_verified' and o.status = 'pending'
    loop
        started := clock_timestamp();
        perform follow60_ordering_v2_test.ack_outbox(receipt);
        insert into follow60_ordering_v2_bench values (
            'outbox_ack', extract(epoch from clock_timestamp() - started) * 1000.0
        );
    end loop;
end;
$$;

select jsonb_pretty(jsonb_object_agg(kind, metrics order by kind))
from (
    select kind, jsonb_build_object(
        'n', count(*),
        'median_ms', round(percentile_cont(0.50) within group (order by duration_ms)::numeric, 6),
        'p90_ms', round(percentile_cont(0.90) within group (order by duration_ms)::numeric, 6),
        'p95_ms', round(percentile_cont(0.95) within group (order by duration_ms)::numeric, 6),
        'mean_ms', round(avg(duration_ms)::numeric, 6),
        'max_ms', round(max(duration_ms)::numeric, 6)
    ) as metrics
    from follow60_ordering_v2_bench
    group by kind
) measured;
