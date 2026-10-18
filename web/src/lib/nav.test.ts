import { describe, expect, it } from "vitest";
import integrationsPage from "../pages/Integrations.tsx?raw";
import { NAV, INTEGRATIONS_ITEM, navItemsFor, phoneMoreItemsFor, phoneTabsFor } from "./nav";

// The navigation as data: one list feeds the rail and the phone, so the rule that nothing in the
// rail is out of reach on a phone is a property of the list, not of two components agreeing.

describe.each([false, true])("with Hiring signals on = %s", (on) => {
  it("splits the destinations between the tab bar and the More sheet without losing or repeating one", () => {
    const rail = navItemsFor(on).map((item) => item.to);
    const phone = [...phoneTabsFor(on), ...phoneMoreItemsFor(on)].map((item) => item.to);
    expect([...phone].sort()).toEqual([...rail].sort());
    expect(new Set(phone).size).toBe(phone.length);
  });

  it("keeps the phone's tab bar to four tabs, so with More it is five", () => {
    expect(phoneTabsFor(on).map((item) => item.label)).toEqual(["Today", "Discover", "Applications", "Profile"]);
  });
});

describe("the entries", () => {
  it("lists Today, Discover, Hiring signals, Applications, Practice and Profile in that order", () => {
    expect(NAV.map((item) => item.label)).toEqual(["Today", "Discover", "Hiring signals", "Applications", "Practice", "Profile"]);
  });

  it("shows Hiring signals only once the server has it on, in the rail and in More", () => {
    expect(navItemsFor(false).map((item) => item.label)).not.toContain("Hiring signals");
    expect(phoneMoreItemsFor(false).map((item) => item.label)).toEqual(["Practice"]);
    expect(phoneMoreItemsFor(true).map((item) => item.label)).toEqual(["Hiring signals", "Practice"]);
  });

  it("gives every entry an icon, and matches Today and Profile on their own address only", () => {
    for (const item of [...NAV, INTEGRATIONS_ITEM]) expect(item.icon.length).toBeGreaterThan(0);
    expect(NAV.filter((item) => item.end).map((item) => item.to)).toEqual(["/", "/profile"]);
  });

  it("points Integrations at its page, which no longer marks Profile", () => {
    expect(INTEGRATIONS_ITEM).toMatchObject({ to: "/profile/integrations", label: "Integrations", end: true });
    expect(NAV.find((item) => item.to === "/profile")?.end).toBe(true);
  });

  it("names the entry exactly as the page it opens is headed, so the label and the copy that says 'Integrations' agree", () => {
    const heading = /<h1>([^<]+)<\/h1>/.exec(integrationsPage)?.[1];
    expect(heading).toBe(INTEGRATIONS_ITEM.label);
  });
});
