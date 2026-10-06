import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { TESTER_AGREEMENT, TESTER_AGREEMENT_VERSION } from "../content/testerAgreement";
import {
  AGREE_LABEL,
  ROLE_COHORTS,
  SENIORITIES,
  SPONSORSHIP_OPTIONS,
  SPONSORSHIP_QUESTION,
  SPONSORSHIP_REASON,
  initialEnrollPageState,
  type EnrollPageState,
  type Enrollment,
} from "../lib/enrollment";
import { anchorsOf, headingLevels, tagsOf, textOfMarkup } from "../testing/markup";
import {
  buttonsLabelled,
  expand,
  findAll,
  onlyButton,
  press,
  prop,
  textOf,
  type HostElement,
} from "../testing/reactTree";
import {
  EnrollmentLoadingView,
  EnrollmentRetryView,
  EnrollmentView,
  type EnrollmentActions,
} from "./EnrollmentView";

// What the enrollment page looks like for each place a person can stand, and that each control is
// wired to the action it is named for. No DOM here: the markup is read as text, and the controls
// are pressed through a plain-element tree (testing/reactTree.ts).

function enrollment(overrides: Partial<Enrollment> = {}): Enrollment {
  return {
    enrolled: true,
    roleCohort: "data_analyst",
    seniority: "mid",
    needsSponsorship: null,
    consentVersion: TESTER_AGREEMENT_VERSION,
    consentedAt: "2026-10-06T09:00:00+00:00",
    withdrawnAt: null,
    currentVersion: TESTER_AGREEMENT_VERSION,
    needsReconsent: false,
    ...overrides,
  };
}

const NOT_JOINED = enrollment({
  enrolled: false,
  roleCohort: null,
  seniority: null,
  consentVersion: null,
  consentedAt: null,
  currentVersion: null,
});
const WITHDRAWN = enrollment({ enrolled: false, withdrawnAt: "2026-10-07T09:00:00+00:00", needsSponsorship: true });
const OLDER = enrollment({ enrolled: false, consentVersion: "2026-01-01", needsReconsent: true });

function recorder() {
  const calls: [string, ...unknown[]][] = [];
  const actions: EnrollmentActions = {
    setRole: (v) => calls.push(["setRole", v]),
    setSeniority: (v) => calls.push(["setSeniority", v]),
    setSponsorship: (v) => calls.push(["setSponsorship", v]),
    setAgreed: (v) => calls.push(["setAgreed", v]),
    submit: () => calls.push(["submit"]),
    askWithdraw: () => calls.push(["askWithdraw"]),
    cancelWithdraw: () => calls.push(["cancelWithdraw"]),
    confirmWithdraw: () => calls.push(["confirmWithdraw"]),
  };
  return { calls, actions };
}

interface Options {
  enrollment?: Enrollment;
  page?: Partial<EnrollPageState>;
  programmeRequired?: boolean;
}

function propsOf(options: Options, actions: EnrollmentActions) {
  return {
    enrollment: options.enrollment ?? NOT_JOINED,
    page: { ...initialEnrollPageState, ...options.page },
    programmeRequired: options.programmeRequired ?? false,
    actions,
  };
}

function html(options: Options = {}): string {
  return renderToStaticMarkup(
    <MemoryRouter>
      <EnrollmentView {...propsOf(options, recorder().actions)} />
    </MemoryRouter>,
  );
}

function tree(options: Options = {}, actions: EnrollmentActions = recorder().actions) {
  return expand(EnrollmentView(propsOf(options, actions)));
}

function control(nodes: ReturnType<typeof tree>, id: string): HostElement {
  const found = findAll(nodes, (el) => el.props.id === id);
  if (found.length !== 1) throw new Error(`expected one #${id}, found ${found.length}`);
  return found[0];
}

function change(element: HostElement, event: unknown): void {
  (prop(element, "onChange") as (e: unknown) => void)(event);
}

describe("the page", () => {
  it("has one h1, then h2 sections, with the agreement's own headings at h3 and no level skipped", () => {
    const levels = headingLevels(html());
    expect(levels[0]).toBe(1);
    expect(levels.filter((level) => level === 1)).toHaveLength(1);
    for (let i = 1; i < levels.length; i++) expect(levels[i] - levels[i - 1]).toBeLessThanOrEqual(1);
    expect(levels.filter((level) => level === 3)).toHaveLength(TESTER_AGREEMENT.sections.length);
  });

  it("is an article of its own, titled, with no <main> (the shell supplies that)", () => {
    const markup = html();
    expect(markup.startsWith('<article class="bj-enroll">')).toBe(true);
    expect(markup).toContain("<h1>Tester programme</h1>");
    expect(markup).not.toContain("<main");
  });
});

describe("the agreement", () => {
  const markup = html();

  it("is shown in full, under its own heading, with its version", () => {
    const text = textOfMarkup(markup);
    expect(text).toContain("Tester Agreement");
    expect(text).toContain(`Version ${TESTER_AGREEMENT_VERSION}`);
    for (const section of TESTER_AGREEMENT.sections) expect(text, section.heading).toContain(section.heading);
    expect(text).toContain("We make no promise that it will work for you");
  });

  it("sits in a labelled region that scrolls and takes focus, so a keyboard can read it all", () => {
    const region = tagsOf(markup, "div").find((tag) => tag.role === "region");
    expect(region).toBeDefined();
    expect(region?.tabindex).toBe("0");
    expect(region?.["aria-label"]).toBe("Tester Agreement, scrollable");
    expect(region?.class).toContain("bj-agreement");
  });

  it("links the Terms and the Privacy Policy, and nothing outside the app but through them", () => {
    const hrefs = anchorsOf(markup).map((anchor) => anchor.attributes.href);
    expect(hrefs).toContain("/privacy");
    expect(hrefs).toContain("/terms");
    for (const href of hrefs) {
      expect(href.startsWith("/") || href.startsWith("#") || href.startsWith("mailto:"), href).toBe(true);
    }
  });

  // The form lives only in this page's state, so a link that replaced the page (an in-app router
  // link) would throw away the role, seniority, sponsorship answer and the ticked box the person
  // had just given, and Back would show an empty form. The page says reading the documents costs
  // them nothing, so EVERY link to another page opens in a new tab: the seven inside the agreement
  // as well as the two in the checkbox sentence. (A mailto and the in-page '#' anchor stay put.)
  it("opens every link to another page of the app in a new tab, the ones inside the agreement too, so reading never loses the form", () => {
    const anchors = anchorsOf(markup);
    const toPages = anchors.filter((anchor) => anchor.attributes.href.startsWith("/"));
    // the agreement's own links (its text names the Terms once and the Privacy Policy several
    // times) and the two in the checkbox sentence
    expect(toPages.length).toBeGreaterThanOrEqual(8);
    expect(new Set(toPages.map((anchor) => anchor.attributes.href))).toEqual(new Set(["/privacy", "/terms"]));
    for (const anchor of toPages) {
      expect(anchor.attributes.target, anchor.attributes.href).toBe("_blank");
      expect(anchor.attributes.rel, anchor.attributes.href).toBe("noopener noreferrer");
      expect(anchor.text, anchor.attributes.href).toContain("(opens in a new tab)");
    }
    // the others are not links to another page
    for (const anchor of anchors.filter((candidate) => !candidate.attributes.href.startsWith("/"))) {
      expect(anchor.attributes.href.startsWith("#") || anchor.attributes.href.startsWith("mailto:")).toBe(true);
      expect(anchor.attributes.target).toBeUndefined();
    }
    // no react-router link is left in the agreement (it renders data-discover)
    expect(markup).not.toContain("data-discover");
  });
});

describe("the form, for someone who has not joined", () => {
  const markup = html();

  it("labels the role and seniority selects, with every option in plain words", () => {
    const labels = [...markup.matchAll(/<label[^>]*for="([^"]+)"/g)].map((match) => match[1]);
    expect(labels).toEqual(expect.arrayContaining(["bj-enroll-role", "bj-enroll-seniority", "bj-enroll-agreed"]));
    const text = textOfMarkup(markup);
    for (const option of [...ROLE_COHORTS, ...SENIORITIES]) expect(text, option.label).toContain(option.label);
    expect(text).toContain("Choose a role");
    expect(text).toContain("Choose your seniority");
    expect(tagsOf(markup, "select").map((tag) => tag.id)).toEqual(["bj-enroll-role", "bj-enroll-seniority"]);
  });

  it("asks the sponsorship question as three radios in a fieldset, with its reason, marked optional", () => {
    const radios = tagsOf(markup, "input").filter((tag) => tag.type === "radio");
    expect(radios.map((tag) => tag.value)).toEqual(SPONSORSHIP_OPTIONS.map((option) => option.value));
    expect(new Set(radios.map((tag) => tag.name)).size).toBe(1);
    const text = textOfMarkup(markup);
    expect(text).toContain(SPONSORSHIP_QUESTION);
    expect(text).toContain("(optional)");
    expect(text).toContain(SPONSORSHIP_REASON);
    expect(text).toContain("Prefer not to say");
    expect(markup).toContain("<fieldset");
    expect(markup).toContain("<legend>");
    expect(tagsOf(markup, "fieldset")[0]["aria-describedby"]).toBe("bj-enroll-sponsorship-reason");
  });

  it("starts with 'Prefer not to say' chosen", () => {
    const radios = tagsOf(markup, "input").filter((tag) => tag.type === "radio");
    const checked = radios.filter((tag) => "checked" in tag);
    expect(checked.map((tag) => tag.value)).toEqual(["not_given"]);
  });

  it("has a required-to-submit checkbox whose label is the agreement sentence, with links to the three documents", () => {
    const checkbox = tagsOf(markup, "input").find((tag) => tag.type === "checkbox");
    expect(checkbox?.id).toBe("bj-enroll-agreed");
    expect("checked" in (checkbox ?? {})).toBe(false);
    const label = markup.match(/<label[^>]*for="bj-enroll-agreed"[^>]*>([\s\S]*?)<\/label>/)?.[1] ?? "";
    expect(textOfMarkup(label).replace(/ \(opens in a new tab\)/g, "")).toBe(AGREE_LABEL);
    const anchors = anchorsOf(label);
    expect(anchors.map((anchor) => anchor.attributes.href)).toEqual(["#bj-agreement-heading", "/terms", "/privacy"]);
    for (const anchor of anchors.slice(1)) {
      expect(anchor.attributes.target).toBe("_blank");
      expect(anchor.attributes.rel).toBe("noopener noreferrer");
    }
  });

  it("has one submit button, 'Join the programme', and no withdraw button", () => {
    const buttons = tagsOf(markup, "button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].type).toBe("submit");
    expect(textOfMarkup(markup)).toContain("Join the programme");
    // (the agreement's own text names the button, so it is the buttons that are counted)
    expect(buttonsLabelled(tree(), "Withdraw from the programme")).toHaveLength(0);
  });

  it("marks the three controls that are needed as required for assistive technology", () => {
    const controls = tagsOf(markup, "select").concat(tagsOf(markup, "input").filter((tag) => tag.type === "checkbox"));
    expect(controls.map((tag) => [tag.id, tag["aria-required"]])).toEqual([
      ["bj-enroll-role", "true"],
      ["bj-enroll-seniority", "true"],
      ["bj-enroll-agreed", "true"],
    ]);
  });

  it("shows no field error until the person has tried to submit", () => {
    expect(textOfMarkup(markup)).not.toContain("Choose the role you are looking for.");
    expect(markup).not.toContain("aria-invalid=\"true\"");
  });

  it("shows each field's error after a try, tied to its field", () => {
    const after = html({ page: { showErrors: true } });
    const text = textOfMarkup(after);
    expect(text).toContain("Choose the role you are looking for.");
    expect(text).toContain("Choose your seniority.");
    expect(text).toContain("Tick the box to say you have read and agree");
    const invalid = tagsOf(after, "select").concat(tagsOf(after, "input")).filter((tag) => tag["aria-invalid"] === "true");
    expect(invalid.map((tag) => tag.id)).toEqual(["bj-enroll-role", "bj-enroll-seniority", "bj-enroll-agreed"]);
    for (const tag of invalid) expect(after).toContain(`id="${tag["aria-describedby"]}"`);
  });
});

describe("the controls are wired to the action each is named for", () => {
  it("the selects, the radios and the checkbox report what was chosen", () => {
    const { actions, calls } = recorder();
    const nodes = tree({}, actions);

    change(control(nodes, "bj-enroll-role"), { target: { value: "qa_sdet" } });
    change(control(nodes, "bj-enroll-seniority"), { target: { value: "lead_plus" } });
    change(control(nodes, "bj-enroll-agreed"), { target: { checked: true } });
    for (const radio of findAll(nodes, (el) => el.props.type === "radio")) change(radio, {});

    expect(calls).toEqual([
      ["setRole", "qa_sdet"],
      ["setSeniority", "lead_plus"],
      ["setAgreed", true],
      ["setSponsorship", "yes"],
      ["setSponsorship", "no"],
      ["setSponsorship", "not_given"],
    ]);
  });

  it("submitting the form submits, and stops the browser's own submit", () => {
    const { actions, calls } = recorder();
    const form = findAll(tree({}, actions), (el) => el.type === "form")[0];
    let prevented = false;

    (prop(form, "onSubmit") as (e: unknown) => void)({ preventDefault: () => (prevented = true) });

    expect(calls).toEqual([["submit"]]);
    expect(prevented).toBe(true);
  });

  it("shows the person's choices", () => {
    const form = {
      roleCohort: "devops_sre",
      seniority: "senior",
      sponsorship: "yes",
      agreed: true,
    } as const;
    const nodes = tree({ page: { form } });
    expect(prop(control(nodes, "bj-enroll-role"), "value")).toBe("devops_sre");
    expect(prop(control(nodes, "bj-enroll-seniority"), "value")).toBe("senior");
    expect(prop(control(nodes, "bj-enroll-agreed"), "checked")).toBe(true);
    const checked = findAll(nodes, (el) => el.props.type === "radio" && el.props.checked === true);
    expect(checked.map((el) => el.props.value)).toEqual(["yes"]);
  });

  it("is locked while a request is out, and says what it is doing", () => {
    const nodes = tree({ page: { busy: "joining" } });
    expect(prop(control(nodes, "bj-enroll-role"), "disabled")).toBe(true);
    expect(prop(control(nodes, "bj-enroll-agreed"), "disabled")).toBe(true);
    const submit = findAll(nodes, (el) => el.type === "button")[0];
    expect(prop(submit, "disabled")).toBe(true);
    expect(textOf(submit)).toBe("Joining...");
  });

  it("will not submit against an agreement the server has replaced, and says to reload", () => {
    const stale = enrollment({ enrolled: false, currentVersion: "2030-01-01" });
    const nodes = tree({ enrollment: stale });
    expect(prop(findAll(nodes, (el) => el.type === "button")[0], "disabled")).toBe(true);
    expect(textOfMarkup(html({ enrollment: stale }))).toContain("older version of the agreement than the server has");
  });

  // The API and the web bundle deploy separately: when the bundle ships first, the SERVER's
  // version is the older one, and "this page shows an older version ... reload" would be false (a
  // reload cannot fix it until the API is updated). The page says which side is behind.
  describe("when the page and the server do not have the same version of the agreement", () => {
    const BEHIND = enrollment({ enrolled: false, withdrawnAt: "2026-10-07T09:00:00+00:00", currentVersion: "2026-09-01" });

    it("says the SERVER has not caught up, with no claim about this page being old, and no reload advice that cannot help", () => {
      const markup = html({ enrollment: BEHIND });
      const text = textOfMarkup(markup);
      expect(text).toContain("The server has not caught up with this version of the agreement yet.");
      expect(text).toContain("Try again in a few minutes. Nothing is recorded until it has.");
      expect(text).not.toContain("older version of the agreement than the server has");
      expect(text).not.toContain("Reload the page");
      // a notice, not an alert: nothing is wrong with this page
      const banner = tagsOf(markup, "div").filter((tag) => tag.role === "status" || tag.role === "alert");
      expect(banner.map((tag) => tag.role)).toContain("status");
      expect(banner.map((tag) => tag.role)).not.toContain("alert");
    });

    it("keeps the join button disabled while the server is the one behind, because the server would refuse it", () => {
      const nodes = tree({ enrollment: BEHIND });
      expect(prop(findAll(nodes, (el) => el.type === "button")[0], "disabled")).toBe(true);
    });

    it("shows the same to a tester who has joined, and does not call this page old", () => {
      const text = textOfMarkup(html({ enrollment: enrollment({ currentVersion: "2026-09-01" }) }));
      expect(text).toContain("The server has not caught up with this version of the agreement yet.");
      expect(text).not.toContain("older version of the agreement than the server has");
    });

    it("says the page is older, in an alert, only when the server's version is the later one", () => {
      const markup = html({ enrollment: enrollment({ enrolled: false, currentVersion: "2030-01-01" }) });
      expect(textOfMarkup(markup)).toContain("This page shows an older version of the agreement than the server has.");
      expect(tagsOf(markup, "div").some((tag) => tag.role === "alert")).toBe(true);
    });

    it("claims no direction, and names both versions, for a version it cannot order", () => {
      const markup = html({ enrollment: enrollment({ enrolled: false, currentVersion: "v2" }) });
      const text = textOfMarkup(markup);
      expect(text).toContain(
        `This page and the server do not show the same version of the agreement (this page: ${TESTER_AGREEMENT_VERSION}; the server: v2).`,
      );
      expect(text).toContain("Reload the page. If they still differ, try again in a few minutes. Nothing is recorded until they match.");
      expect(text).not.toContain("older version");
      expect(text).not.toContain("has not caught up");
      expect(prop(findAll(tree({ enrollment: enrollment({ enrolled: false, currentVersion: "v2" }) }), (el) => el.type === "button")[0], "disabled")).toBe(true);
    });

    it("says nothing about versions when they are the same", () => {
      const text = textOfMarkup(html({ enrollment: NOT_JOINED }));
      expect(text).not.toContain("has not caught up");
      expect(text).not.toContain("older version");
      expect(text).not.toContain("do not show the same version");
    });
  });
});

describe("an error from the server", () => {
  it("is shown in an alert that can take focus, and nowhere when there is none", () => {
    const withError = html({ page: { error: "The agreement changed." } });
    const alert = tagsOf(withError, "div").find((tag) => tag.role === "alert" && tag.tabindex === "-1");
    expect(alert).toBeDefined();
    expect(textOfMarkup(withError)).toContain("The agreement changed.");
    expect(tagsOf(html(), "div").filter((tag) => tag.role === "alert")).toHaveLength(0);
  });
});

describe("for a tester who has joined", () => {
  const joined = enrollment({ needsSponsorship: false });

  it("says what they gave: role, seniority, the sponsorship answer, and the agreement and day", () => {
    const text = textOfMarkup(html({ enrollment: joined }));
    expect(text).toContain("What you gave us");
    expect(text).toContain("Data analyst");
    expect(text).toContain("Mid-level");
    expect(text).toContain("Sponsorship answer No");
    expect(text).toContain(`Version ${TESTER_AGREEMENT_VERSION}, on October 6, 2026`);
  });

  it("shows 'Prefer not to say' for a null answer, never 'No'", () => {
    expect(textOfMarkup(html({ enrollment: enrollment({ needsSponsorship: null }) }))).toContain(
      "Sponsorship answer Prefer not to say",
    );
  });

  it("has no join form, and the agreement is still there to read", () => {
    const markup = html({ enrollment: joined });
    expect(tagsOf(markup, "form")).toHaveLength(0);
    expect(textOfMarkup(markup)).toContain("Tester Agreement");
  });

  it("offers 'Withdraw from the programme', which only asks first", () => {
    const { actions, calls } = recorder();
    press(onlyButton(tree({ enrollment: joined }, actions), "Withdraw from the programme"));
    expect(calls).toEqual([["askWithdraw"]]);
  });

  it("asks for confirmation before withdrawing, and says what it does and does not do", () => {
    const { actions, calls } = recorder();
    const nodes = tree({ enrollment: joined, page: { confirmingWithdraw: true } }, actions);
    const text = textOfMarkup(html({ enrollment: joined, page: { confirmingWithdraw: true } }));
    expect(text).toContain("Withdraw from the tester programme?");
    // the operator's roster report still counts a withdrawn tester, by role: so "out of the
    // reports" would be too much to promise
    expect(text).toContain("leaves your usage out of the programme\u2019s counts, apart from a count of how many people withdrew, by role");
    expect(text).not.toContain("leaves you out of the programme");
    expect(text).toContain("does not delete your account or your data");
    // where the programme is not required, withdrawing changes nothing about the features, so the
    // confirmation says nothing about starting them or about background work (the agreement below
    // it does, which is why this reads the confirmation's own paragraph)
    const confirmation = textOfMarkup(
      html({ enrollment: joined, page: { confirmingWithdraw: true } }).match(/<p id="bj-enroll-confirm-text">([\s\S]*?)<\/p>/)?.[1] ?? "",
    );
    expect(confirmation).toContain("does not delete your account or your data.");
    expect(confirmation).not.toContain("starting them");
    expect(confirmation).not.toContain("keep running");

    press(onlyButton(nodes, "Yes, withdraw"));
    press(onlyButton(nodes, "Keep me in the programme"));
    expect(calls).toEqual([["confirmWithdraw"], ["cancelWithdraw"]]);
  });

  it("adds that withdrawing stops them STARTING the costly features, and that saved searches and Gmail reply checking keep running, where the server requires the programme", () => {
    const text = textOfMarkup(html({ enrollment: joined, page: { confirmingWithdraw: true }, programmeRequired: true }));
    // the saved-search matcher and the Gmail reply checker run on a schedule and ask nothing about
    // enrollment (tests/test_tester_enrollment_gate.py pins that), so "closes them" was false
    const confirmation = textOfMarkup(
      html({ enrollment: joined, page: { confirmingWithdraw: true }, programmeRequired: true }).match(
        /<p id="bj-enroll-confirm-text">([\s\S]*?)<\/p>/,
      )?.[1] ?? "",
    );
    expect(confirmation).toContain("withdrawing also stops you starting them until you join again");
    expect(confirmation).toContain("Saved searches and Gmail reply checking keep running until you pause, delete or disconnect them.");
    expect(confirmation).not.toContain("closes them");
    expect(text).not.toContain("closes them");
  });

  it("gives the Withdraw button, the confirmation and the result banner ids and tabindex -1 for focus to land on", () => {
    // After a press the control the person used is gone, and focus would fall to the document.
    const ask = html({ enrollment: joined });
    expect(tagsOf(ask, "button").find((tag) => tag.id === "bj-enroll-withdraw")).toBeDefined();

    const confirming = html({ enrollment: joined, page: { confirmingWithdraw: true } });
    const group = tagsOf(confirming, "div").find((tag) => tag.id === "bj-enroll-confirm");
    expect(group?.role).toBe("group");
    expect(group?.tabindex).toBe("-1");
    expect(group?.["aria-describedby"]).toBe("bj-enroll-confirm-text");
    expect(confirming).toContain('id="bj-enroll-confirm-text"');
    // the Withdraw button is replaced by the confirmation, so it is not drawn twice
    expect(tagsOf(confirming, "button").filter((tag) => tag.id === "bj-enroll-withdraw")).toHaveLength(0);

    for (const notice of ["joined", "withdrew"] as const) {
      const banner = tagsOf(html({ enrollment: notice === "joined" ? joined : WITHDRAWN, page: { notice } }), "div").find(
        (tag) => tag.id === "bj-enroll-notice",
      );
      expect(banner?.role, notice).toBe("status");
      expect(banner?.tabindex, notice).toBe("-1");
    }
    // no result banner, no focus target for one
    expect(html({ enrollment: joined })).not.toContain("bj-enroll-notice");
  });

  it("locks the buttons while the withdrawal is out", () => {
    const nodes = tree({ enrollment: joined, page: { confirmingWithdraw: true, busy: "withdrawing" } });
    expect(prop(onlyButton(nodes, "Withdrawing..."), "disabled")).toBe(true);
    expect(prop(onlyButton(nodes, "Keep me in the programme"), "disabled")).toBe(true);
  });

  it("says they are in, with a way back to Today, right after joining", () => {
    const text = textOfMarkup(html({ enrollment: joined, page: { notice: "joined" } }));
    expect(text).toContain("You are in the tester programme. Thank you.");
    expect(text).toContain("Go to Today");
  });
});

describe("for someone who withdrew", () => {
  it("says when, that nothing was deleted, and offers 'Join again' with their old answers in the form", () => {
    const markup = html({
      enrollment: WITHDRAWN,
      page: { form: { roleCohort: "data_analyst", seniority: "mid", sponsorship: "yes", agreed: false } },
    });
    const text = textOfMarkup(markup);
    expect(text).toContain("You withdrew from the tester programme on October 7, 2026.");
    expect(text).toContain("Your account and your data are unchanged");
    expect(text).toContain("Join again");
    expect(buttonsLabelled(tree({ enrollment: WITHDRAWN }), "Withdraw from the programme")).toHaveLength(0);
  });

  it("says so once, right after withdrawing, without repeating the date line", () => {
    const text = textOfMarkup(html({ enrollment: WITHDRAWN, page: { notice: "withdrew" } }));
    expect(text).toContain("You have withdrawn from the tester programme.");
    expect(text).not.toContain("You withdrew from the tester programme on");
  });
});

describe("for a tester on an older agreement", () => {
  const markup = html({ enrollment: OLDER });

  it("says the agreement changed, which version they accepted and which is current, and asks them to accept the new one", () => {
    const text = textOfMarkup(markup);
    expect(text).toContain("The tester agreement has changed since you accepted it.");
    expect(text).toContain(`You accepted version 2026-01-01; the current version is ${TESTER_AGREEMENT_VERSION}`);
    expect(text).toContain("Accept the new agreement");
  });

  it("still lets them withdraw", () => {
    expect(textOfMarkup(markup)).toContain("Withdraw from the programme");
  });
});

describe("where the programme is required", () => {
  it("says so above the form, and which pages stay open: Profile (deleting the account) and Integrations (removing a key, disconnecting Gmail, pausing or deleting a saved search)", () => {
    const text = textOfMarkup(html({ programmeRequired: true }));
    expect(text).toContain("Joining the tester programme is required on this server.");
    expect(text).toContain("the rest of the app opens when you join");
    expect(text).toContain(
      "These pages stay open: your Profile page, where you can delete your account, and your Integrations page, where you can remove a key, disconnect Gmail, and pause or delete a saved search.",
    );
  });

  it("says nothing of the kind where it is not, or once they have joined", () => {
    expect(textOfMarkup(html())).not.toContain("is required on this server");
    expect(textOfMarkup(html({ enrollment: enrollment(), programmeRequired: true }))).not.toContain(
      "is required on this server",
    );
  });
});

describe("the loading and retry views", () => {
  it("say plainly that the lookup is in progress, claiming nothing about the person", () => {
    const markup = renderToStaticMarkup(<EnrollmentLoadingView />);
    expect(textOfMarkup(markup)).toBe("Tester programme Checking where you stand.");
  });

  it("say that the lookup failed, with the reason, in an alert, and a retry that is wired", () => {
    let retried = 0;
    const markup = renderToStaticMarkup(
      <EnrollmentRetryView message="The service is down." programmeRequired onRetry={() => retried++} />,
    );
    expect(markup).toContain('role="alert"');
    const text = textOfMarkup(markup);
    expect(text).toContain("We could not check your tester enrollment");
    expect(text).toContain("The service is down.");
    expect(text).toContain("Nothing was changed");
    press(onlyButton(expand(EnrollmentRetryView({ message: "x", programmeRequired: true, onRetry: () => retried++ })), "Try again"));
    expect(retried).toBe(1);
  });

  it("name the server's rule only where the server has one", () => {
    const required = textOfMarkup(renderToStaticMarkup(<EnrollmentRetryView message="x" programmeRequired onRetry={() => {}} />));
    const optional = textOfMarkup(renderToStaticMarkup(<EnrollmentRetryView message="x" programmeRequired={false} onRetry={() => {}} />));
    expect(required).toContain("This server asks testers to join the programme before they use the costly features.");
    expect(optional).toContain("Nothing was changed.");
    expect(optional).not.toContain("This server asks testers");
  });
});
