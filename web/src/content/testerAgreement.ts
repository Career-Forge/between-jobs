// The Tester Agreement, as typed data in the shape legal.ts uses for the Privacy Policy and the
// Terms. The enrollment page (pages/Enroll.tsx) draws it, and testerAgreement.test.ts pins its
// structure and checks what it says against the code.
//
// WHAT IS WRITTEN HERE MUST BE TRUE OF THE CODE AS IT IS, exactly as legal.ts says of its own
// text. Where a sentence is a commitment that no code in this repository enforces (the 30-day
// deletion of programme data, reports kept to counts, looking at one tester's records only in the
// ways listed, a capped and revocable AI key), it is a promise by whoever runs the service, and the
// operator approves the wording before it is served. Where a sentence says what the product does
// NOT stop doing (saved searches and Gmail reply checking after a withdrawal), it is true because
// tests/test_tester_enrollment_gate.py fails when either worker starts asking about enrollment.
//
// TESTER_AGREEMENT_VERSION is what a consent record points at: the enrollment row stores the
// version a person accepted (tester_enrollments.consent_version), and the API refuses an
// acceptance that names any other version. The same string is TESTER_AGREEMENT_VERSION in
// src/between_jobs/api/tester_enrollment.py; tests/test_tester_enrollment.py reads THIS file and
// fails when the two differ. Changing the text of the agreement means a new value in both places:
// everyone who accepted the old one is then asked to accept again.

import { OPERATOR_ACCESS_LIMIT, type Block, type Inline, type LegalDocument } from "./legal";
import { PRIVACY_EMAIL } from "./site";

export const TESTER_AGREEMENT_VERSION = "2026-10-06";

const p = (...inline: Inline[]): Block => ({ kind: "p", inline });
const ul = (...items: (string | Inline[])[]): Block => ({
  kind: "ul",
  items: items.map((item) => (typeof item === "string" ? [item] : item)),
});

const privacyEmail: Inline = { email: PRIVACY_EMAIL };
const terms: Inline = { to: "/terms", text: "Terms of Service" };
const policy: Inline = { to: "/privacy", text: "Privacy Policy" };

export const TESTER_AGREEMENT: LegalDocument = {
  title: "Tester Agreement",
  sections: [
    {
      id: "what-this-is",
      heading: "What this is",
      blocks: [
        p(
          "The tester programme is a limited period in which people looking for work in a set list of roles use Between Jobs, so that we can see how well it works for them. By joining you agree to this agreement, the ",
          terms,
          " and the ",
          policy,
          ". Joining is your choice, and you can leave at any time.",
        ),
      ],
    },
    {
      id: "what-we-record",
      heading: "What we record about you, and why",
      blocks: [
        p("When you join, we record:"),
        ul(
          "your role, picked from a fixed list (for example data analyst or software engineer), and your seniority, picked from a fixed list of bands (new graduate, early career, mid-level, senior, lead or above);",
          "your answer to the optional sponsorship question below, if you give one;",
          "which version of this agreement you accepted and when, the day your record was first made, and when you withdrew, if you do;",
          "the usage records that the Privacy Policy describes under 'Product usage events': counts and outcomes, never what you typed, your resume or any job text, a web address, your IP address or details of your browser. The product keeps them for every account, not only for testers.",
        ),
        p(
          "We record these to see how far people in each role and level get, and where they get stuck, and to report on how the programme went. Those reports are counts by role and level. 'Who can see it' says when we look at one tester's records.",
        ),
        p(
          "The services that handle your data, the ones that run Between Jobs and your AI provider, are listed in the ",
          policy,
          " under 'Who else handles your data'. Joining the programme adds none.",
        ),
      ],
    },
    {
      id: "sponsorship",
      heading: "The sponsorship question",
      blocks: [
        p(
          "We ask whether you would need an employer to sponsor your right to work. That is about your immigration situation, so we treat it as sensitive.",
        ),
        ul(
          "Answering is optional. You can say yes or no, or choose 'Prefer not to say'.",
          "If you do not answer, we record that no answer was given. We never read that as a 'no'.",
          "The product never uses your answer. It changes nothing you see or anything written for you.",
          "We ask only so that our reports can tell a product that fails people who need sponsorship apart from a job market that does. The answer is used in those counts and nowhere else, and it is deleted with the rest of the programme data (see 'Deleting your data').",
        ),
      ],
    },
    {
      id: "who-can-see",
      heading: "Who can see it",
      blocks: [
        p(
          "The person who runs Between Jobs can technically read everything stored about you, your programme records included. The ",
          policy,
          " explains why, under 'What the operator can see'.",
        ),
        p(
          `We do not read a tester's data as a matter of routine. ${OPERATOR_ACCESS_LIMIT}`,
        ),
        p(
          "The programme adds two cases to that. If your programme records show you stopped at a setup step and have not got past it, we may look at them and contact you to offer help. If you tell us a generated resume contains something you never did, we will open it and compare it with your profile.",
        ),
        p(
          "To report on the programme we count the programme records by role and level. Those counts involve no resume, job or answer text.",
        ),
      ],
    },
    {
      id: "gmail",
      heading: "Gmail is optional",
      blocks: [
        ul(
          "You never have to connect Gmail to be a tester.",
          [
            "If you do connect it, the ",
            policy,
            " says what Between Jobs does with it, under 'Gmail (optional)'. You can disconnect it at any time with 'Disconnect Gmail' on the Integrations page.",
          ],
        ),
      ],
    },
    {
      id: "deleting",
      heading: "Deleting your data",
      blocks: [
        ul(
          [
            "'Delete my account' on the Profile page removes your account and everything tied to it straight away, your programme records included. What a deletion does not reach, such as database backups until they expire, is listed in the ",
            policy,
            " under 'Keeping and deleting your data'.",
          ],
          [
            "You can also ask us to delete your account by email at ",
            privacyEmail,
            ". We will do it within 7 days.",
          ],
          "Whether or not you delete your account, we delete your enrollment record and your usage records 30 days after the programme ends, unless you have agreed that we may keep them. Deleting them does not delete your account.",
        ),
      ],
    },
    {
      id: "leaving",
      heading: "Leaving the programme",
      blocks: [
        ul(
          "You can withdraw at any time with 'Withdraw from the programme' on the programme page, which the Profile page links to.",
          "Withdrawing marks your enrollment as withdrawn and records when. It does not delete your account or your data. Deleting is a separate step, above.",
          "From then on we leave your usage out of the programme's counts, apart from a count of how many people withdrew, by role. The product keeps recording the same usage records it keeps for every account, until you delete your account.",
          "Where joining is required to use the costly features (the ones that call an AI, a search or a document service), withdrawing also stops you starting them until you join again. It does not stop what already runs in the background: saved searches, which use your AI key, and Gmail reply checking. Pause or delete a saved search, or disconnect Gmail, on the Integrations page to stop them.",
        ),
      ],
    },
    {
      id: "ai-keys",
      heading: "An AI key from us, if you are given one",
      blocks: [
        p(
          "This part applies only if we give you an AI key to use during the programme.",
        ),
        ul(
          "The key has a spending cap, and we can revoke it at any time. When it is revoked, the features that need it stop working until you save a key of your own.",
          [
            "You save it on the Integrations page like a key of your own, and it is stored encrypted in the same way. Your AI provider receives what the ",
            policy,
            " says it receives for any key.",
          ],
        ),
      ],
    },
    {
      id: "no-promises",
      heading: "No promise of results",
      blocks: [
        p(
          "Between Jobs is early software. We make no promise that it will work for you, that it will always be available, or that it will get you an interview or a job. The ",
          terms,
          " say the same for everyone.",
        ),
      ],
    },
    {
      id: "changes",
      heading: "If this agreement changes",
      blocks: [
        p(
          "This agreement has a version, shown at the top, and your enrollment record keeps the version you accepted. If the agreement changes, the version changes and you will be asked to accept the new one.",
        ),
      ],
    },
  ],
};
