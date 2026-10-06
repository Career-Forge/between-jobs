import { describe, expect, it } from "vitest";
import { splitPersonName, type NameSplitIssue } from "@/lib/personName";

// A spelling heuristic, tested as a table of name shapes. The rule is stated in
// lib/personName.ts; what these pin is that it only rearranges the words it was given, and
// that a part it cannot know is null (empty on the form) with the reason, never a guess.

type Row = [description: string, input: string, first: string | null, last: string | null, issue?: NameSplitIssue];

const ROWS: Row[] = [
  // ordinary
  ["two words", "Jane Doe", "Jane", "Doe"],
  ["extra whitespace and newlines", "  Jane \n  Doe\t", "Jane", "Doe"],

  // one word: there is no last name to place
  ["a single name", "Madonna", "Madonna", null, "single_name"],
  ["a single name with a suffix", "Madonna Jr.", "Madonna Jr.", null, "single_name"],
  ["a single name in Cyrillic", "Сергей", "Сергей", null, "single_name"],

  // several given names: everything before the family name is given names
  ["two given names", "Mary Jane Watson", "Mary Jane", "Watson"],
  ["three given names", "John Paul George Ringo", "John Paul George", "Ringo"],
  ["initials", "J. R. R. Tolkien", "J. R. R.", "Tolkien"],
  ["a middle initial", "John Q. Public", "John Q.", "Public"],
  ["a bare V as a middle initial is not a suffix", "John V Smith", "John V", "Smith"],

  // particles stay with the family name
  ["van der", "Jan van der Berg", "Jan", "van der Berg"],
  ["van", "Ludwig van Beethoven", "Ludwig", "van Beethoven"],
  ["von", "Wernher von Braun", "Wernher", "von Braun"],
  ["da", "Leonardo da Vinci", "Leonardo", "da Vinci"],
  ["de la", "Maria de la Cruz", "Maria", "de la Cruz"],
  ["di", "Leonardo di Caprio", "Leonardo", "di Caprio"],
  ["bin", "Omar bin Laden", "Omar", "bin Laden"],
  ["al", "Hassan al Sayed", "Hassan", "al Sayed"],
  ["a particle after several given names", "Anna Maria van den Heuvel", "Anna Maria", "van den Heuvel"],
  ["a capitalised Van is not read as a particle", "Eddie Van Halen", "Eddie Van", "Halen"],
  ["a particle never leaves the given name empty", "van Berg", "van", "Berg"],

  // suffixes stay with the family name
  ["Jr.", "John Smith Jr.", "John", "Smith Jr."],
  ["Jr without a dot", "John Smith Jr", "John", "Smith Jr"],
  ["III", "Robert Downey III", "Robert", "Downey III"],
  ["a suffix after a comma", "John Smith, Jr.", "John", "Smith Jr."],
  ["a post-nominal after a comma", "Jane Doe, PhD", "Jane", "Doe PhD"],
  ["a suffix and a particle", "Jan van der Berg Jr.", "Jan", "van der Berg Jr."],
  ["a long name with a suffix", "Martin Luther King Jr.", "Martin Luther", "King Jr."],

  // every listed suffix, on its own (each one is pinned, not masked by another)
  ["MBA", "Jane Doe MBA", "Jane", "Doe MBA"],
  ["Sr.", "John Smith Sr.", "John", "Smith Sr."],
  ["II", "John Smith II", "John", "Smith II"],
  ["IV", "John Smith IV", "John", "Smith IV"],
  ["MD", "John Smith MD", "John", "Smith MD"],
  ["Esq", "Jane Doe Esq", "Jane", "Doe Esq"],
  ["CPA", "Jane Doe CPA", "Jane", "Doe CPA"],
  ["MSc", "Jane Doe MSc", "Jane", "Doe MSc"],
  ["BSc", "Jane Doe BSc", "Jane", "Doe BSc"],
  ["PMP", "John Smith PMP", "John", "Smith PMP"],
  ["RN", "Jane Doe RN", "Jane", "Doe RN"],
  ["CFA", "Jane Doe CFA", "Jane", "Doe CFA"],
  ["PE", "John Smith PE", "John", "Smith PE"],
  ["MS", "Jane Doe MS", "Jane", "Doe MS"],
  ["DDS", "John Smith DDS", "John", "Smith DDS"],
  ["DVM", "Jane Doe DVM", "Jane", "Doe DVM"],
  ["CISSP", "John Smith CISSP", "John", "Smith CISSP"],
  ["CSM", "Jane Doe CSM", "Jane", "Doe CSM"],
  ["NP", "Jane Doe NP", "Jane", "Doe NP"],
  // dotted post-nominals are the same words
  ["Ph.D.", "Jane Doe Ph.D.", "Jane", "Doe Ph.D."],
  ["M.D.", "John Smith M.D.", "John", "Smith M.D."],
  ["B.Sc.", "Jane Doe B.Sc.", "Jane", "Doe B.Sc."],
  ["Ph.D. after a comma", "Jane Doe, Ph.D.", "Jane", "Doe Ph.D."],
  ["M.D. after a comma", "Jane Doe, M.D.", "Jane", "Doe M.D."],
  ["PMP after a comma", "Jane Doe, PMP", "Jane", "Doe PMP"],
  ["RN after a comma", "Jane Doe, RN", "Jane", "Doe RN"],
  ["CFA after a comma", "Jane Doe, CFA", "Jane", "Doe CFA"],
  ["PE after a comma", "Jane Doe, PE", "Jane", "Doe PE"],
  ["MS after a comma", "Jane Doe, MS", "Jane", "Doe MS"],
  ["MA after a comma", "Jane Doe, MA", "Jane", "Doe MA"],
  ["BA after a comma", "Jane Doe, BA", "Jane", "Doe BA"],
  ["PA after a comma", "Jane Doe, PA", "Jane", "Doe PA"],
  ["several after commas", "Jane Doe, MBA, PMP", "Jane", "Doe MBA PMP"],
  ["stacked suffixes", "John Smith Jr. PhD", "John", "Smith Jr. PhD"],
  ["family, given, suffix keeps every word", "Smith, John, Jr.", "John", "Smith Jr."],
  ["family, given, post-nominal", "Doe, Jane, PhD", "Jane", "Doe PhD"],
  ["a suffix after a comma on a single word", "Madonna, Jr.", "Madonna Jr.", null, "single_name"],

  // a surname that is also a post-nominal is a surname, unless a comma says otherwise
  ["Ma as a surname", "Jack Ma", "Jack", "Ma"],
  ["Ba as a surname", "Mariama Ba", "Mariama", "Ba"],

  // a credential this file does not list is labelled unknown, never put in the first-name box
  ["an unlisted credential after a comma", "Jane Doe, XYZ", null, null, "order_unknown"],
  ["an all-caps word after a comma behind a mixed-case family", "Van Der Berg, JAN", null, null, "order_unknown"],
  ["an all-caps given name in an all-caps name is a name", "SMITH, JOHN", "JOHN", "SMITH"],
  ["an all-caps name with a multi-word family is read as family, given", "VAN DER BERG, JAN", "JAN", "VAN DER BERG"],
  ["an all-caps given name behind a one-word family is a name", "Smith, JOHN", "JOHN", "Smith"],

  // stray commas
  ["a trailing comma", "Jane Doe,", "Jane", "Doe"],
  ["a leading comma", ", Jane Doe", "Jane", "Doe"],
  ["only commas", " , ,", null, null],

  // hyphenated words stay whole
  ["a hyphenated given name", "Jean-Luc Picard", "Jean-Luc", "Picard"],
  ["a hyphenated family name", "Anna Smith-Jones", "Anna", "Smith-Jones"],
  ["both hyphenated", "Anne-Marie Smith-Jones", "Anne-Marie", "Smith-Jones"],

  // "Family, Given" as written
  ["family, given", "Smith, John", "John", "Smith"],
  ["family, several given", "Watson, Mary Jane", "Mary Jane", "Watson"],

  // honorifics at the front are not a name part (each one is pinned)
  ["Mrs.", "Mrs. Jane Doe", "Jane", "Doe"],
  ["Ms.", "Ms. Jane Doe", "Jane", "Doe"],
  ["Miss", "Miss Jane Doe", "Jane", "Doe"],
  ["Mx.", "Mx. Sam Doe", "Sam", "Doe"],
  ["Mr.", "Mr. John Smith", "John", "Smith"],
  ["Sir", "Sir Ian McKellen", "Ian", "McKellen"],
  ["Dame", "Dame Judi Dench", "Judi", "Dench"],
  ["stacked honorifics", "Dr. Prof. Jane Doe", "Jane", "Doe"],
  ["Dr.", "Dr. Jane Doe", "Jane", "Doe"],
  ["Prof.", "Prof. Jane Q. Doe", "Jane Q.", "Doe"],
  ["an honorific alone is the whole name", "Dr", "Dr", null, "single_name"],

  // case is kept exactly as written; in all caps a particle is still a particle
  ["all caps", "JANE DOE", "JANE", "DOE"],
  ["all caps with a particle", "LUDWIG VAN BEETHOVEN", "LUDWIG", "VAN BEETHOVEN"],
  ["all caps with a suffix", "JOHN SMITH JR", "JOHN", "SMITH JR"],
  ["lower case", "jane doe", "jane", "doe"],

  // accents and other Latin-script letters
  ["accented letters", "José García", "José", "García"],
  ["a decomposed accent is composed, not lost", "José Garcia", "José", "Garcia"],
  ["Greek", "Γιώργος Παπαδόπουλος", "Γιώργος", "Παπαδόπουλος"],
  ["Cyrillic", "Иван Петров", "Иван", "Петров"],

  // scripts whose word order the spelling does not reveal: unknown, never guessed
  ["Japanese with a space", "山田 太郎", null, null, "order_unknown"],
  ["Japanese without one", "山田太郎", null, null, "order_unknown"],
  ["Katakana", "タロウ ヤマダ", null, null, "order_unknown"],
  ["Korean", "김 민수", null, null, "order_unknown"],
  ["Chinese", "王 小明", null, null, "order_unknown"],
  ["a Latin name with one Han word", "Wang 小明", null, null, "order_unknown"],
  ["Thai", "สมชาย ใจดี", null, null, "order_unknown"],
  ["Lao", "ສົມຊາຍ ໃຈດີ", null, null, "order_unknown"],
  ["Khmer", "សុខ ចាន់", null, null, "order_unknown"],
  ["Myanmar", "မောင် မောင်", null, null, "order_unknown"],
  ["Tibetan", "བསྟན་འཛིན་ རྒྱ་མཚོ", null, null, "order_unknown"],
  ["Hiragana", "やまだ たろう", null, null, "order_unknown"],

  // nothing to split
  ["empty", "", null, null],
  ["only whitespace", "  \n ", null, null],
];

describe("splitPersonName", () => {
  it.each(ROWS)("%s: %j", (_description, input, first, last, issue = undefined) => {
    expect(splitPersonName(input)).toEqual({ first, last, issue: issue ?? null });
  });

  it("only rearranges: every word it was given is in the result, in order, except an honorific or a comma", () => {
    const inputs = ROWS.filter(([, , first, last]) => first !== null && last !== null).map(([, input]) => input);
    for (const input of inputs) {
      const { first, last } = splitPersonName(input);
      const words = (text: string) => text.normalize("NFC").replace(/[,]/gu, " ").split(/\s+/u).filter(Boolean);
      const given = words(first ?? "");
      const family = words(last ?? "");
      const original = words(input);
      // every word of the result is a word of the input (nothing invented) ...
      for (const word of [...given, ...family]) expect(original).toContain(word);
      // ... and nothing but a leading honorific went missing
      const dropped = original.filter((w) => !given.includes(w) && !family.includes(w));
      expect(dropped.every((w) => /^(dr|prof|mr|mrs|ms|miss|mx|sir|dame)\.?$/iu.test(w))).toBe(true);
    }
  });

  it("never reports an issue together with a part it filled for the same reason", () => {
    expect(splitPersonName("Madonna")).toEqual({ first: "Madonna", last: null, issue: "single_name" });
    expect(splitPersonName("山田 太郎")).toEqual({ first: null, last: null, issue: "order_unknown" });
  });

  it("handles a very long name without trouble", () => {
    const long = `${"Anna ".repeat(5000)}Smith`;
    const start = performance.now();
    const result = splitPersonName(long);
    expect(performance.now() - start).toBeLessThan(200);
    expect(result.last).toBe("Smith");
  });
});
