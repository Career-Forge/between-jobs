// The shapes of the next-step engine: what a page tells it, what it answers, and what a rule is.
// Pure types -- nothing here runs. See engine.ts for how rules are evaluated and index.ts for how a
// page registers its own.

// What the cyan button (or a link styled as it) does. The engine never holds a handler: it names
// the action and the page that owns the band maps `intent` to a real function.
export type NextStepAction =
  /** Goes to one of the app's own routes (a hash is fine: "/profile#import-review"). */
  | { kind: "link"; label: string; to: string }
  /** Runs a handler the page registered under `intent`. */
  | { kind: "button"; label: string; intent: string }
  /** Submits the form with this id (a button outside the form, via the `form` attribute). */
  | { kind: "submit"; label: string; form: string }
  /** Opens another site; the band never submits anything for the person. */
  | { kind: "external"; label: string; href: string };

export interface NextStep {
  /** One plain sentence, chosen by code. Never written by a model. */
  sentence: string;
  /** The page's one cyan action, or null for an unlit band (nothing needs you, or the choice is per row). */
  action: NextStepAction | null;
  /** The facts that picked this step, each read from the input; drawn as `why: a · b`. */
  why: readonly string[];
  /** "failure" draws the red cross before the sentence. Absent means an ordinary step. */
  tone?: "failure";
  /** One outlined button beside the cyan one, only where the rule table names it. Never a second cyan. */
  secondary?: NextStepAction | null;
  /** The action exists but is locked until the person finishes something; `hint` names what unlocks it. */
  locked?: { hint: string };
}

// What the engine answers. `loading` means an input the answer depends on has not arrived, so the
// band shows its eyebrow and "Loading..." and nothing else -- never a guess that is later replaced.
// `none` means this page has no next step at all, and the band renders nothing.
export type NextStepOutcome = { status: "loading" } | { status: "none" } | { status: "ready"; step: NextStep };

// A read the page made to learn something about the account. `at` is the client time it failed,
// already formatted by the caller (this module has no clock).
export type SetupCheck<T> =
  | { state: "loading" }
  | { state: "failed"; call: string; at: string }
  | { state: "ready"; value: T };

export interface ProfileFacts {
  /** An activated profile version exists (GET /profile/current answered with one). */
  active: boolean;
  /** An import is waiting for review. Known only in the session that created it. */
  importWaiting: boolean;
}

// Nothing is read from the credentials list yet: the setup check can only be loading, failed or
// ready. The model-key rows will widen this when they are registered.
export type CredentialsFacts = Record<string, never>;

export interface PageFailure {
  /** What failed, as a sentence start: "Couldn't load Today". */
  what: string;
  /** The request that failed: "GET /today". */
  call: string;
  /** The client time it failed, formatted by the caller: "09:14". */
  at: string;
  /** Which handler "Try again" runs. Defaults to "retry". */
  intent?: string;
}

// Everything a page can read, handed to the engine. A check the page did not supply counts as
// still loading: the engine never fills in a state it was not told.
export interface NextStepInput<F = unknown> {
  /** Which page is asking: "landing", "login", "today", "settings". */
  page: string;
  signedIn: boolean;
  profile?: SetupCheck<ProfileFacts>;
  credentials?: SetupCheck<CredentialsFacts>;
  /** The page's own data request failed (network or 5xx): the F1 row. */
  pageFailure?: PageFailure | null;
  /** The sign-in page's mode; it changes only the label on the button. */
  authMode?: "sign-in" | "register";
  /** Page-specific facts, typed by the page's own rows file. */
  facts?: F;
}

// "wait": an input this row needs has not arrived. The whole answer is `loading`, and no later row
// is tried (falling through to one could light a step that is replaced a moment later).
// "none": this page has no next step. null: this row does not apply, try the next.
export type RowResult = NextStep | "wait" | "none" | null;

export interface NextStepRow<F = unknown> {
  /** The rule table's own name for the row: "G1", "T3". Tests key on it. */
  id: string;
  match(input: NextStepInput<F>): RowResult;
}

export interface PageRules<F = unknown> {
  /** Matches `input.page`. */
  page: string;
  rows: readonly NextStepRow<F>[];
}

export function definePage<F>(page: string, rows: readonly NextStepRow<F>[]): PageRules<F> {
  return { page, rows };
}
