import { describe, expect, it } from "vitest";
import type { PrepareApplicationResult } from "./generateTypes";
import { gmailFactOf, generationFact, type GenerationInputs } from "./workspaceFacts";

const READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly";

function result(overrides: Partial<PrepareApplicationResult> = {}): PrepareApplicationResult {
  return {
    run_id: "r1",
    resume: { artifact_id: "a", version_id: "v", media_type: "application/pdf", sha256: "00" },
    ats_attempts: [],
    final_score: null,
    score_scale: "ats_100",
    warnings: [],
    ...overrides,
  };
}

function inputs(overrides: Partial<GenerationInputs>): GenerationInputs {
  return { read: "done", state: { kind: "idle" }, hasResume: false, downloaded: false, ...overrides };
}

const FIT = { overall_score: 3.6, dimensions: {}, gaps: [], strengths: [], recommendation: "Skip", keyword_gaps: [], keyword_hits: [], visa_flag: null };

describe("what the band is told about the resume", () => {
  it.each([
    ["a request is out", inputs({ state: { kind: "generating" }, hasResume: true }), { state: "generating" }],
    ["the request failed in this visit", inputs({ state: { kind: "error", message: "x", at: "09:14" } }), { state: "failed", at: "09:14" }],
    ["the stored result is still being read", inputs({ read: "loading" }), { state: "reading" }],
    ["the stored result is being read, but a run in this visit already has an answer", inputs({ read: "loading", state: { kind: "ready", restored: false, result: result() } }), { state: "drafted", flagged: 0, downloaded: false }],
    ["nothing is stored and no resume exists", inputs({}), { state: "none" }],
    ["the read failed and no resume exists", inputs({ read: "failed" }), { state: "none" }],
    ["a resume exists and there is no result to count its claims in", inputs({ hasResume: true }), { state: "drafted", flagged: null, downloaded: false }],
    ["the read failed and a resume exists", inputs({ read: "failed", hasResume: true }), { state: "drafted", flagged: null, downloaded: false }],
    ["a drafted result with two flagged claims and a caution", inputs({ state: { kind: "ready", restored: true, result: result({ warnings: ['unsupported claim (unverifiable): "a" -- b', 'unsupported claim: "c" -- d', "engine caution"] }) }, hasResume: true }), { state: "drafted", flagged: 2, downloaded: false }],
    ["a drafted result that was downloaded in this visit", inputs({ state: { kind: "ready", restored: false, result: result() }, hasResume: true, downloaded: true }), { state: "drafted", flagged: 0, downloaded: true }],
    ["held back, no earlier resume", inputs({ state: { kind: "ready", restored: true, result: result({ resume: null, fit: FIT }) } }), { state: "held_back", ceiling: 3.6, earlierResume: false }],
    ["held back, an earlier resume still exists", inputs({ state: { kind: "ready", restored: true, result: result({ resume: null, fit: FIT }) }, hasResume: true }), { state: "held_back", ceiling: 3.6, earlierResume: true }],
    ["held back with a fit read that has no usable score", inputs({ state: { kind: "ready", restored: true, result: result({ resume: null, fit: { ...FIT, overall_score: Number.NaN } }) } }), { state: "held_back", ceiling: null, earlierResume: false }],
    ["a result with no resume and no fit, nothing earlier", inputs({ state: { kind: "ready", restored: true, result: result({ resume: null, fit: null }) } }), { state: "none" }],
    ["a result with no resume and no fit, an earlier resume exists", inputs({ state: { kind: "ready", restored: true, result: result({ resume: null }) }, hasResume: true }), { state: "drafted", flagged: null, downloaded: false }],
    ["a setup failure, which is not a failed run", inputs({ state: { kind: "setup", notice: { message: "m", linkTo: null, linkLabel: null } } }), { state: "none" }],
  ])("%s", (_name, given, expected) => {
    expect(generationFact(given)).toEqual(expected);
  });
});

describe("what the band is told about Gmail", () => {
  const connected = [{ service: "oauth", provider: "gmail", is_validated: true, scope: READ_SCOPE }];

  it.each([
    ["open to the person and connected", "available", connected, "connected"],
    ["open to the person and not connected", "available", [], "not_connected"],
    ["open to the person, connected, but Google stopped accepting it", "available", [{ ...connected[0], is_validated: false }], "reconnect_needed"],
    ["open to the person, connected before read access was asked for", "available", [{ ...connected[0], scope: null }], "reconnect_needed"],
    ["not open to this person: a stored connection is not claimed", "not_for_you", connected, "not_connected"],
    ["not on this server", "not_on_server", [], "not_connected"],
    ["not known whether it is open", "unknown", connected, "unknown"],
    ["open, but the credential list is not read", "available", null, "unknown"],
  ] as const)("%s -> %s", (_name, access, rows, expected) => {
    expect(gmailFactOf(access, rows)).toBe(expected);
  });
});
