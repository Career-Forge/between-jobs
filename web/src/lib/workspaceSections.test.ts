import { describe, expect, it } from "vitest";
import { CROSS_NAV_HASH } from "./applicationsBoard";
import { SECTION_IDS, SECTION_TITLES, workspaceJumpGroups } from "./workspaceSections";

describe("the workspace's sections", () => {
  it("are the ones the Applications menu and the other pages link to, one anchor each, none repeated", () => {
    expect(Object.values(SECTION_IDS).sort()).toEqual(Object.values(CROSS_NAV_HASH).sort());
    expect(new Set(Object.values(SECTION_IDS)).size).toBe(Object.values(SECTION_IDS).length);
  });

  it("are indexed by three labelled groups in the order of the page", () => {
    const groups = workspaceJumpGroups(true);
    expect(groups.map((group) => group.label)).toEqual(["Resume", "Research", "Prepare"]);
    expect(groups.flatMap((group) => group.links.map((link) => `${link.label}:${link.id}`))).toEqual([
      "Tailor:ch-sources",
      "Draft:ch-draft",
      "Generate:generate",
      "Review:ch-check",
      "Company intel:company-intel",
      "Contacts:contacts",
      "Warm-path events:warm-path-events",
      "Hiring posts:hiring-posts",
      "Interview practice:interview-practice",
      "Your play:your-play",
    ]);
  });

  it("name each section once: the jump bar's label for an id is the section's title", () => {
    expect(Object.keys(SECTION_TITLES).sort()).toEqual(Object.keys(SECTION_IDS).sort());
    const byId = new Map<string, string>(Object.entries(SECTION_IDS).map(([key, id]) => [id, SECTION_TITLES[key as keyof typeof SECTION_TITLES]]));
    for (const group of workspaceJumpGroups(true)) for (const link of group.links) expect(link.label).toBe(byId.get(link.id));
  });

  it("leaves Hiring posts out unless the server is known to have it, and makes the group labels no anchors", () => {
    expect(workspaceJumpGroups(false).flatMap((group) => group.links.map((link) => link.label))).not.toContain("Hiring posts");
    const ids = workspaceJumpGroups(true).flatMap((group) => group.links.map((link) => link.id));
    for (const label of ["ch-research", "ch-prepare", "ch-resume"]) expect(ids).not.toContain(label);
  });
});
