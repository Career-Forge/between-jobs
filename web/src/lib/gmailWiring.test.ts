import { describe, expect, it } from "vitest";
import useCapabilitiesSource from "./useCapabilities.ts?raw";
import useGmailConnectionSource from "./useGmailConnection.ts?raw";

// The two hooks that bind the pure modules to React run effects. A test without a DOM can still
// run them, by recording `useEffect` and calling what was registered (useCapabilities.test.ts does,
// and that is what proves the order inside its effect); these checks read the source, as a cheap
// backstop for the lines that test does not exercise. The deciding the hooks hand to is tested in
// gmailAccess.test.ts and capabilitiesStore.test.ts.

describe("useGmailConnection", () => {
  it("reads the person's credentials once, and only when Gmail is open to them", () => {
    expect(useGmailConnectionSource).toContain('apiFetch<GmailCredentialRow[]>("/credentials")');
    expect(useGmailConnectionSource).toContain("if (!enabled) return;");
    expect(useGmailConnectionSource).toContain("}, [enabled]);");
  });

  it("leaves the connection unknown when the read fails, and ignores an answer that arrives after it was left", () => {
    expect(useGmailConnectionSource).toContain("if (!cancelled) setRows(loaded);");
    expect(useGmailConnectionSource).toContain("cancelled = true;");
    expect(useGmailConnectionSource).not.toContain("setRows([])");
  });

  it("lets a refused request prove the reconnect, through the one function that decides what that means", () => {
    expect(useGmailConnectionSource).toContain("const markReconnectNeeded = useCallback(() => setProven(true), []);");
    expect(useGmailConnectionSource).toContain("afterReconnectRefusal(gmailConnection(rows), proven)");
  });
});

describe("useCapabilities and whose answer it holds", () => {
  it("tells the store who is signed in, and asks only for someone who is", () => {
    expect(useCapabilitiesSource).toContain("const userId = useAuth().session?.user.id ?? null;");
    expect(useCapabilitiesSource).toContain("store.setPerson(userId);");
    expect(useCapabilitiesSource).toContain("if (userId !== null) store.ensure();");
    expect(useCapabilitiesSource).toContain("}, [userId]);");
  });

  it("tells the store who is signed in BEFORE it asks, so the next person gets their own ask", () => {
    // The order is run, not just read, in useCapabilities.test.ts; this keeps it visible here too.
    const told = useCapabilitiesSource.indexOf("store.setPerson(userId);");
    const asked = useCapabilitiesSource.indexOf("store.ensure();");
    expect(told).toBeGreaterThan(-1);
    expect(asked).toBeGreaterThan(told);
  });
});
