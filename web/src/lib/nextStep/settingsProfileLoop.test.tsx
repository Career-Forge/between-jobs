// @vitest-environment jsdom
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { ResumeImportView, type ResumeImportActions } from "../../components/ResumeImportView";
import { render } from "../../testing/render";
import type { ModelKeyReading } from "../modelKey";
import { initialImportState } from "../resumeImport";
import { SETTINGS_PATH } from "../settingsPaths";
import { nextStepWithRow, type NextStepInput } from "./index";
import { settingsOutcome, type SettingsFacts } from "./pages/settings";

// Two pages could each tell a person with no profile and no usable model key to go to the other, and
// neither would get them anywhere: the Settings band sends them to the Profile page to import a profile,
// and the Profile page's file card sends them to Settings for the key the import runs on. This test
// reads the two links from the code that makes them and pins that, for every state of the key, the two
// are never both there.
//
// The Settings side is settingsOutcome's answer for an account with no active profile. The Profile side
// is the anchors a rendered ResumeImportView draws for the same key reading, not its markup or text.
//
// NOT COVERED: a key the provider accepts when it is saved and then refuses to use (no credit left, say).
// Both pages read that as a usable key, so the import fails at the provider, its notice links to Settings,
// and Settings then says "Set up your profile". That is one bounce, and it is a known and accepted one.
//
// Only the file card's own link is read: the Profile page has other links to Settings in its body copy,
// which this does not look at.

// Written as a Record so that a reading added to ModelKeyReading is a type error here until it is covered.
const KEY_STATES: Record<ModelKeyReading, SettingsFacts["llm"]> = {
  none: { state: "none" },
  needs_model: { state: "needs_model", providerName: "OpenAI" },
  needs_key: { state: "needs_key", providerName: "OpenAI" },
  ready: { state: "ready", names: ["OpenAI"], defaultKnown: true },
};
const READINGS = Object.keys(KEY_STATES) as ModelKeyReading[];

function pathOf(address: string): string {
  return address.split("#")[0];
}

// The Settings page for an account that has read its keys and has no active profile.
function settingsLinksToProfile(reading: ModelKeyReading): boolean {
  const input: NextStepInput<SettingsFacts> = {
    page: "settings",
    signedIn: true,
    profile: { state: "ready", value: { active: false, importWaiting: false } },
    credentials: { state: "ready", value: {} },
    facts: { llm: KEY_STATES[reading], chosenHere: null },
  };
  const outcome = settingsOutcome(nextStepWithRow(input), input);
  if (outcome.status !== "ready") return false;
  const action = outcome.step.action;
  return action !== null && action.kind === "link" && pathOf(action.to) === "/profile";
}

function nothing(): ResumeImportActions {
  const none = () => undefined;
  return { chooseFile: none, fileChosen: none, retry: none, close: none, toggleSource: none, askUse: none, cancelUse: none, confirmUse: none, discard: none };
}

// The file card as the Profile page draws it for a person who has chosen no file, given what the page
// read about their keys.
function profileCardLinksToSettings(reading: ModelKeyReading): boolean {
  const { container, unmount } = render(
    <MemoryRouter>
      <ResumeImportView state={initialImportState} replacesCurrent={false} actions={nothing()} modelKey={reading} />
    </MemoryRouter>,
  );
  const links = [...container.querySelectorAll("a")].map((anchor) => pathOf(anchor.getAttribute("href") ?? ""));
  unmount();
  return links.includes(SETTINGS_PATH);
}

describe("Settings and the Profile file card", () => {
  it.each(READINGS)("never both send a person with no active profile to the other, for a key reading of %s", (reading) => {
    expect(settingsLinksToProfile(reading) && profileCardLinksToSettings(reading)).toBe(false);
  });

  it("do each send the person on where the key is the missing piece, so the pair above is not passing because a link is never found", () => {
    // The file card asks for the key by linking to Settings, for every reading that is not usable.
    expect(READINGS.filter(profileCardLinksToSettings)).toEqual(["none", "needs_model", "needs_key"]);
    // Settings asks for the profile, by linking to the Profile page, only once the key is usable.
    expect(READINGS.filter(settingsLinksToProfile)).toEqual(["ready"]);
  });
});
