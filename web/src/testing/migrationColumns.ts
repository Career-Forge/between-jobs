// The columns of a table, read from the SQL migrations as text (the package has no database and no
// DOM). The Privacy Policy and the Tester Agreement each say what is recorded about a person, and
// the tests that hold them to the schema (content/legal.test.ts, content/testerAgreement.test.ts)
// must not miss a column, so this takes ANY column definition whatever its type (a whitelist of
// types would let a jsonb or inet column through unnamed), and follows a later migration's
// `alter table ... add / drop / rename column`, in the order the migrations run. Only used by tests.

/** The columns `table` has after `migrations` (SQL texts, in the order they run) have been
 *  applied. Throws unless exactly one of them creates the table. */
export function columnsIn(migrations: readonly string[], table: string): string[] {
  const createTable = new RegExp(`create\\s+table\\s+public\\.${table}\\s*\\(`, "i");
  const creators = migrations.filter((sql) => createTable.test(sql));
  if (creators.length !== 1) {
    throw new Error(`expected exactly one migration to create public.${table}, found ${creators.length}`);
  }
  const body = creators[0].split(createTable)[1].split(/\n\);/)[0];
  let columns = body
    .split("\n")
    .map(
      (line) =>
        /^\s{2}(?!constraint\b|primary\b|foreign\b|unique\b|check\b|like\b)([a-z_]+)\s+[a-z]/i.exec(line)?.[1],
    )
    .filter((name): name is string => name !== undefined);

  const alter = new RegExp(
    `^\\s*alter\\s+table\\s+(?:if\\s+exists\\s+)?(?:only\\s+)?(?:public\\.)?${table}\\b([\\s\\S]*)$`,
    "i",
  );
  for (const statement of migrations.flatMap((sql) => sql.replace(/--[^\n]*/g, "").split(";"))) {
    const rest = alter.exec(statement)?.[1];
    if (rest === undefined) continue;
    for (const match of rest.matchAll(/\badd\s+column\s+(?:if\s+not\s+exists\s+)?([a-z_]+)/gi)) {
      columns.push(match[1]);
    }
    for (const match of rest.matchAll(/\bdrop\s+column\s+(?:if\s+exists\s+)?([a-z_]+)/gi)) {
      columns = columns.filter((name) => name !== match[1]);
    }
    for (const match of rest.matchAll(/\brename\s+column\s+([a-z_]+)\s+to\s+([a-z_]+)/gi)) {
      columns = columns.map((name) => (name === match[1] ? match[2] : name));
    }
  }
  return columns;
}

/** The same, for the migrations a `import.meta.glob(..., { query: "?raw", eager: true })` returned
 *  (keys are paths, which sort in migration order because each starts with its timestamp). */
export function columnsOfTable(sources: Record<string, string>, table: string): string[] {
  return columnsIn(
    Object.entries(sources)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, sql]) => sql),
    table,
  );
}
