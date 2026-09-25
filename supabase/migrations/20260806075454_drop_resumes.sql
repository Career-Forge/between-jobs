-- Retires the Sprint 2.4 raw-text resume stub (Sprint 2.5e) -- fully
-- replaced by profile_versions/career_facts. Nothing reads or writes this
-- table anymore (resumes.py and its tests are deleted in the same
-- commit). Dropping the table takes its trigger and RLS policies with it.

drop table public.resumes;
