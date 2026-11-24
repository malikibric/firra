-- L3-01: processing_attempts on source_record is a small bounded counter that starts at 0.
begin;
\ir _helpers.psql
select no_plan();

select has_column('public', 'source_record', 'processing_attempts', 'source_record counts processing attempts');
select col_type_is('public', 'source_record', 'processing_attempts', 'smallint', 'the counter is a smallint');
select col_not_null('public', 'source_record', 'processing_attempts', 'the counter is never null');
select col_default_is('public', 'source_record', 'processing_attempts', '0', 'the counter starts at zero');
select ok(
  exists (
    select 1 from pg_constraint c join pg_class t on t.oid = c.conrelid
    where t.relname = 'source_record' and c.contype = 'c'
      and pg_get_constraintdef(c.oid) like '%processing_attempts%'
  ),
  'the counter has a range check');

select * from finish();
rollback;
