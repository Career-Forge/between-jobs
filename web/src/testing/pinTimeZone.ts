// Vitest global setup: runs the whole suite in one time zone, whatever zone the machine is in.
//
// The pages write a date in the reader's own zone (lib/trail.ts), which is right for a person and
// wrong for a test: an instant a few hours after midnight UTC is "Sep 22" in London and "Sep 21" in
// Los Angeles. Without a pin, `npm test` passes or fails with the contributor's clock. The suite
// runs in UTC unless TEST_TZ names another zone, which is how to check a page against one:
//
//   TEST_TZ=America/New_York npm test
//
// The fixtures are written so that the suite also passes from UTC-10 to UTC+11 (an instant in a
// fixture sits mid-day UTC, not near midnight). Zones past that range are covered by the pin alone.
//
// A shell that exports TZ (a developer in Jersey City, say) is deliberately not honoured: that is the
// dependence this removes.

// @types/node is not installed in this package; only the environment is used.
declare const process: { env: Record<string, string | undefined> };

export default function setup(): void {
  process.env.TZ = process.env.TEST_TZ ?? "UTC";
}
