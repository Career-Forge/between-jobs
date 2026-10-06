-- When a remembered answer was last filled into a form.
--
-- `times_used` has existed since the table was created but nothing ever incremented it. The
-- extension now reports each fill that used a remembered answer exactly as stored, and the
-- backend records the use: it adds one to `times_used` and stamps this column in the same
-- update. Null means "never used", which is every row that exists today.
--
-- Additive and nullable on purpose: no default, no backfill, no new index, and no change to
-- any policy or grant (the table's existing ones cover a new column). Code that reads or writes
-- the column must not be deployed before this migration is applied.
alter table public.approved_answers
  add column last_used_at timestamptz;
