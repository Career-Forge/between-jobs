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

interface StepWords {
  /** One plain sentence, chosen by code. Never written by a model. */
  sentence: string;
  /** The facts that picked this step, each read from the input; drawn as `why: a · b`. */
  why: readonly string[];
  /** "failure" draws the red cross before the sentence. Absent means an ordinary step. */
  tone?: "failure";
}

/** An unlit band: nothing needs you, or the choice is per row. It has no button, so it can carry neither a secondary nor a lock. */
export interface UnlitStep extends StepWords {
  action: null;
  secondary?: undefined;
  locked?: undefined;
}

/** A lit band: the page's one cyan action. */
export interface LitStep extends StepWords {
  action: NextStepAction;
  /** One outlined button beside the cyan one, only where the rule table names it. Never a second cyan. */
  secondary?: NextStepAction | null;
  /** The action exists but is locked until the person finishes something; `hint` names what unlocks it. */
  locked?: { hint: string };
}

export type NextStep = UnlitStep | LitStep;

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

// Nothing is read from the credentials list yet: the check can only be loading, failed or ready.
// A row that needs a value from it will widen this when it is registered.
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

// Everything a page can read, handed to the engine. The engine never fills in a state it was not
// told: a profile check the page did not supply counts as still loading, and a credentials check
// it did not supply means the page did not read the credentials at all (nothing waits for it).
export interface NextStepInput<F = unknown> {
  /** Which page is asking: "landing", "login", "today", "settings". */
  page: string;
  signedIn: boolean;
  profile?: SetupCheck<ProfileFacts>;
  /** Supply it only if the page called GET /credentials; leave it out otherwise. */
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
  /** The row's name in the rule table: "G1", "L0". Unique across the global rows and every page. Tests key on it. */
  id: string;
  match(input: NextStepInput<F>): RowResult;
}

export interface PageRules<F = unknown> {
  /** Matches `input.page`. */
  page: string;
  rows: readonly NextStepRow<F>[];
  /**
   * Checks that `input.facts` really has the shape this page's rows read. Without it a row sees
   * whatever the caller passed (the string-keyed entry point cannot tie a page name to a type).
   * When it answers false the page's rows are not run, so the answer is `none` -- the engine does
   * not guess a step from facts it cannot read.
   */
  facts?: (value: unknown) => boolean;
}

export function definePage<F>(
  page: string,
  rows: readonly NextStepRow<F>[],
  options: { facts?: (value: unknown) => value is F } = {},
): PageRules<F> {
  return options.facts === undefined ? { page, rows } : { page, rows, facts: options.facts };
}
