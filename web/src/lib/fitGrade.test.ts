import { describe, expect, it } from "vitest";
import type { ScoreBin } from "./discoverTypes";
import { GRADE_LABEL, GRADE_LADDER, gradeOfBin, isEngineBin, isFitGrade, type FitGrade } from "./fitGrade";

describe("gradeOfBin", () => {
  it("shows the engine's four bins as three grades: Strong is Good, Good is Decent, Mixed and Poor are Bad fit", () => {
    const shown: Record<ScoreBin, FitGrade> = { Strong: "good", Good: "decent", Mixed: "bad", Poor: "bad" };
    for (const [bin, grade] of Object.entries(shown)) {
      expect(gradeOfBin(bin), bin).toBe(grade);
    }
  });

  it("never shows the engine's bin 'Good' under the word Good: that word is the top grade only", () => {
    expect(GRADE_LABEL[gradeOfBin("Good") as FitGrade]).toBe("Decent");
    expect(GRADE_LABEL[gradeOfBin("Strong") as FitGrade]).toBe("Good");
    const binsShownAsGood = (["Strong", "Good", "Mixed", "Poor"] as const).filter((bin) => gradeOfBin(bin) === "good");
    expect(binsShownAsGood).toEqual(["Strong"]);
  });

  it("has no grade for anything that is not one of the engine's bins, and never guesses one", () => {
    const unknown = ["Excellent", "strong", " Strong", "Strong ", "good", "decent", "bad", "Bad fit", "Decent", "", "constructor", "__proto__", "toString", "hasOwnProperty", null, undefined, 3, {}, ["Strong"]];
    for (const bin of unknown) {
      expect(gradeOfBin(bin), JSON.stringify(bin)).toBeNull();
    }
  });
});

describe("isEngineBin", () => {
  it("is true for exactly the values that have a grade", () => {
    const values = ["Strong", "Good", "Mixed", "Poor", "Excellent", "strong", "good", "Bad fit", "", "constructor", "__proto__", null, undefined, 3, {}, ["Strong"]];
    for (const value of values) {
      expect(isEngineBin(value), JSON.stringify(value)).toBe(gradeOfBin(value) !== null);
    }
    expect(values.filter((value) => isEngineBin(value))).toEqual(["Strong", "Good", "Mixed", "Poor"]);
  });
});

describe("the grades", () => {
  it("are a ladder of three, lowest first", () => {
    expect(GRADE_LADDER).toEqual(["bad", "decent", "good"]);
  });

  it("read Good, Decent and Bad fit, and no grade reads as an engine bin other than Good", () => {
    expect(GRADE_LABEL).toEqual({ good: "Good", decent: "Decent", bad: "Bad fit" });
    for (const label of Object.values(GRADE_LABEL)) {
      expect(["Strong", "Mixed", "Poor"]).not.toContain(label);
    }
  });

  it("have a word for every rung", () => {
    expect(Object.keys(GRADE_LABEL).sort()).toEqual([...GRADE_LADDER].sort());
  });
});

describe("isFitGrade", () => {
  it("is true for the three grades and false for a bin, a label or anything else", () => {
    for (const grade of GRADE_LADDER) expect(isFitGrade(grade)).toBe(true);
    for (const other of ["Strong", "Good", "Bad fit", "GOOD", "", "constructor", null, undefined, 0, {}]) {
      expect(isFitGrade(other), JSON.stringify(other)).toBe(false);
    }
  });
});
