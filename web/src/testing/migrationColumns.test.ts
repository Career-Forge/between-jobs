import { describe, expect, it } from "vitest";
import { columnsIn, columnsOfTable } from "./migrationColumns";

const migrationSources = import.meta.glob("../../../supabase/migrations/*.sql", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("columnsIn, which the column guards in the content tests depend on", () => {
  const CREATE = `create table public.widgets (
  id uuid primary key,
  note text not null check (char_length(note) > 0),
  extra jsonb,
  seen_from inet,
  label varchar(40),
  amount numeric(10, 2),
  born date,
  tags text[],
  score smallint,
  constraint widgets_label_key unique (label)
);`;

  it("finds a column whatever its type, and no constraint", () => {
    expect(columnsIn([CREATE], "widgets")).toEqual([
      "id",
      "note",
      "extra",
      "seen_from",
      "label",
      "amount",
      "born",
      "tags",
      "score",
    ]);
  });

  it("follows a later migration's add, drop and rename of a column, in order", () => {
    const later = `alter table public.widgets add column visa_status text;
alter table if exists only public.widgets add column if not exists region text, drop column born;
alter table public.widgets rename column note to remark;
alter table public.other add column unrelated text;`;
    expect(columnsIn([CREATE, later], "widgets")).toEqual([
      "id",
      "remark",
      "extra",
      "seen_from",
      "label",
      "amount",
      "tags",
      "score",
      "visa_status",
      "region",
    ]);
  });

  it("ignores a comment, and does not read a table whose name merely starts the same", () => {
    const later = `-- alter table public.widgets add column from_a_comment text;
alter table public.widgets_archive add column other text;`;
    expect(columnsIn([CREATE, later], "widgets")).not.toContain("from_a_comment");
    expect(columnsIn([CREATE, later], "widgets")).not.toContain("other");
  });

  it("refuses to guess when no migration, or more than one, creates the table", () => {
    expect(() => columnsIn([], "widgets")).toThrow(/exactly one/);
    expect(() => columnsIn([CREATE, CREATE], "widgets")).toThrow(/exactly one/);
  });

  it("reads the two tables as they stand in the repository's migrations", () => {
    expect(columnsOfTable(migrationSources, "tester_enrollments")).toContain("withdrawn_at");
    expect(columnsOfTable(migrationSources, "product_events")).toContain("duration_ms");
  });
});
