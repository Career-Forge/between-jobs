import { describe, expect, it } from "vitest";
import { MAX_MAILTO_LENGTH, buildMailto, mailtoRecipient, subjectAndBody } from "./mailto";

function href(draft: { to: string | null; subject: string; body: string }): string {
  const link = buildMailto(draft);
  if (link.href === null) throw new Error("expected a link");
  return link.href;
}

// What a mail app reads back out of the link.
function parsed(link: string): { to: string; subject: string; body: string } {
  const [head, query] = link.slice("mailto:".length).split("?");
  const params = new URLSearchParams(query);
  return { to: decodeURIComponent(head), subject: params.get("subject") ?? "", body: params.get("body") ?? "" };
}

describe("the ceiling", () => {
  it("is 1,800 characters, under the roughly 2,000 where Outlook and older Windows handlers stop", () => {
    expect(MAX_MAILTO_LENGTH).toBe(1800);
  });
});

describe("the recipient", () => {
  it("is the address the person looked up, when it is a plain address", () => {
    expect(href({ to: "jane.doe@acme.example", subject: "s", body: "b" })).toMatch(/^mailto:jane\.doe@acme\.example\?/);
    expect(mailtoRecipient("  Jane_Doe+jobs@mail.acme.co.uk ")).toBe("Jane_Doe+jobs@mail.acme.co.uk");
    expect(mailtoRecipient("o'brien@acme.example")).toBe("o'brien@acme.example");
  });

  it("goes into the link trimmed, and with a plus sign encoded", () => {
    expect(href({ to: "  Jane_Doe+jobs@mail-eu.acme-corp.example ", subject: "s", body: "b" })).toMatch(
      /^mailto:Jane_Doe%2Bjobs@mail-eu\.acme-corp\.example\?subject=s&body=b$/,
    );
  });

  it("may have hyphens in the host", () => {
    expect(mailtoRecipient("jane@mail-eu.acme-corp.example")).toBe("jane@mail-eu.acme-corp.example");
  });

  it("is left empty when there is none, and the mail app asks who to write to", () => {
    expect(href({ to: null, subject: "s", body: "b" })).toMatch(/^mailto:\?subject=/);
    expect(mailtoRecipient(null)).toBeNull();
    expect(mailtoRecipient(undefined)).toBeNull();
    expect(mailtoRecipient("")).toBeNull();
  });

  it.each([
    "a@b.example,c@d.example", // a second recipient
    "a@b.example;c@d.example",
    "a@b.example?cc=x@y.example", // a header
    "a@b.example&bcc=x@y.example",
    "a b@c.example",
    "a@b",
    "@b.example",
    "a@@b.example",
    "a%40b.example@c.example",
    '"a@b.example"@c.example',
    "<a@b.example>",
    "a@b.example\r\nBcc: x@y.example",
    "javascript:alert(1)",
    "a@b.example/",
    "a@b.example#x",
    // the same characters in the part before the @
    "a,b@c.example",
    "a;b@c.example",
    "a:b@c.example",
    "a?b@c.example",
    "a&b@c.example",
    "a=b@c.example",
    "a#b@c.example",
    "a/b@c.example",
    "a%b@c.example",
    "a<b@c.example",
    'a"b@c.example',
    "a\\b@c.example",
    "a(b)@c.example",
    "a b@c.example",
  ])("is dropped, never repaired, when it is not a plain address: %j", (address) => {
    expect(mailtoRecipient(address)).toBeNull();
    const link = href({ to: address, subject: "s", body: "b" });
    expect(link.startsWith("mailto:?subject=")).toBe(true);
    expect(link).not.toContain("x@y.example");
  });
});

describe("the subject and the body", () => {
  it("come back exactly as written when a mail app decodes the link", () => {
    const body = "Hi Jane,\n\nI saw your talk -- 100% worth it & more?\nBest,\nA";
    const link = href({ to: null, subject: "Loved your talk: a question?", body });
    const back = parsed(link);
    expect(back.subject).toBe("Loved your talk: a question?");
    expect(back.body).toBe(body.replace(/\n/g, "\r\n"));
  });

  it("cannot add a header or a second recipient, however they are written", () => {
    const link = href({
      to: "jane@acme.example",
      subject: "Hello&cc=evil@x.example",
      body: "Hi?bcc=evil@x.example&to=evil@x.example\r\nCc: evil@x.example",
    });
    // the link has exactly the recipient, one subject and one body
    const query = link.split("?").slice(1).join("?");
    expect([...new URLSearchParams(query).keys()]).toEqual(["subject", "body"]);
    expect(link.split("?")).toHaveLength(2);
    expect(link).not.toMatch(/[&?](cc|bcc|to)=/);
    expect(parsed(link).subject).toBe("Hello&cc=evil@x.example");
  });

  it("keeps the subject to one line and uses CRLF for the body's line breaks", () => {
    const link = href({ to: null, subject: "One\nTwo\r\nThree\t four ", body: "a\nb\r\nc\rd" });
    expect(parsed(link).subject).toBe("One Two Three four");
    expect(parsed(link).body).toBe("a\r\nb\r\nc\r\nd");
  });

  it("drops control characters, at both ends of each range", () => {
    const link = href({
      to: null,
      subject: "A\u0000B\u0007C\u0008D\u000bE\u000cF\u000eG\u001fH",
      body: "x\u0000y\u0008z\u000b\u000cw\u000ev\u001fu\u007ft\u0080s\u0085r\u009fq",
    });
    expect(parsed(link).subject).toBe("ABCDEFGH");
    expect(parsed(link).body).toBe("xyzwvutsrq");
  });

  it("keeps a tab, a line break and the first printable characters after the ranges", () => {
    const link = href({ to: null, subject: "A\tB", body: "a\tb\nc \u00a0d\u00a1e" });
    expect(parsed(link).subject).toBe("A B");
    expect(parsed(link).body).toBe("a\tb\r\nc \u00a0d\u00a1e");
  });

  it("keeps non-English text intact", () => {
    const link = href({ to: null, subject: "Grüße aus München", body: "你好 こんにちは" });
    expect(parsed(link).subject).toBe("Grüße aus München");
    expect(parsed(link).body).toBe("你好 こんにちは");
  });
});

describe("the length ceiling", () => {
  it("allows a link right up to it", () => {
    const empty = href({ to: null, subject: "", body: "" }).length;
    const body = "a".repeat(MAX_MAILTO_LENGTH - empty);
    const link = buildMailto({ to: null, subject: "", body });
    expect(link.href?.length).toBe(MAX_MAILTO_LENGTH);
  });

  it("gives no link, and says why, one character past it", () => {
    const empty = href({ to: null, subject: "", body: "" }).length;
    const body = "a".repeat(MAX_MAILTO_LENGTH - empty + 1);
    expect(buildMailto({ to: null, subject: "", body })).toEqual({ href: null, reason: "too_long" });
  });

  it("counts the encoded length, which is what a mail app is handed", () => {
    // 600 characters of ü is 1,200 bytes of UTF-8 and 3,600 characters once percent-encoded
    expect(buildMailto({ to: null, subject: "", body: "ü".repeat(600) })).toEqual({
      href: null,
      reason: "too_long",
    });
  });

  it("never cuts a body short to make it fit", () => {
    const long = buildMailto({ to: null, subject: "s", body: "word ".repeat(1000) });
    expect(long.href).toBeNull();
  });
});

describe("the text to copy", () => {
  it("is the subject on its own line, a blank line, and the body as written", () => {
    expect(subjectAndBody({ subject: "Hello\nthere", body: "Line one\nLine two" })).toBe(
      "Subject: Hello there\n\nLine one\nLine two",
    );
  });
});
