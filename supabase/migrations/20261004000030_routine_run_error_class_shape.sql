-- Security review of milestone E (DeepSeek, comment on #157): `routine_run.error_class` is readable by every
-- workspace member, and a future writer passing `str(error)` would store content there. The worker writes
-- `type(error).__name__`; allow only identifier-shaped names. Existing rows are class names or null.
alter table public.routine_run
  add constraint routine_run_error_class_is_a_name
  check (error_class is null or error_class ~ '^[A-Za-z][A-Za-z0-9_]{2,79}$');
