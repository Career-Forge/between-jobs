// @vitest-environment jsdom
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Personal } from "../lib/profileTypes";
import { settle, typeInto } from "../testing/forms";
import { buttonNamed, click, query, render } from "../testing/render";
import { PersonalDetailsCard } from "./PersonalDetailsCard";

// Pressing Save is what makes the draft clean, which used to disable the button and drop keyboard focus to the
// top of the page. The button now stays where it is, and says nothing is left to save.

function Host({ fail = false }: { fail?: boolean }) {
  const [personal, setPersonal] = useState<Personal>({ name: "Asha Verma", work_authorization: "US citizen." });
  return (
    <PersonalDetailsCard
      personal={personal}
      saving={false}
      onSave={async (mutate) => {
        if (fail) throw new Error("nope");
        const next = mutate({ personal } as never) as unknown as { personal: Personal };
        setPersonal(next.personal);
      }}
    />
  );
}

describe("keyboard focus on Save in Personal details", () => {
  it("stays on the button after a save, which is then aria-disabled because nothing changed", async () => {
    const { container } = render(<Host />);
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "US citizen. Edited.");
    const save = buttonNamed(container, "Save");
    expect(save.getAttribute("aria-disabled")).toBeNull();
    save.focus();
    click(save);
    await settle();
    expect(document.activeElement).toBe(save);
    expect(save.disabled).toBe(false);
    expect(save.getAttribute("aria-disabled")).toBe("true");
  });

  it("stays on the button after a failed save, which can be pressed again", async () => {
    const { container } = render(<Host fail />);
    typeInto(query<HTMLTextAreaElement>(container, "textarea"), "US citizen. Edited.");
    const save = buttonNamed(container, "Save");
    save.focus();
    click(save);
    await settle();
    expect(document.activeElement).toBe(save);
    expect(save.getAttribute("aria-disabled")).toBeNull();
    expect(container.querySelector("[role='alert']")).not.toBeNull();
  });
});
