import ts from "typescript";
import { textNamesForbiddenControl } from "@/lib/forbiddenControls";

// The machinery behind tests/neverSubmit.test.ts. Two independent nets:
//
//  - `scanSource` reads source code (comments excluded: it works on the syntax tree) and
//    reports anything that could aim at, activate or submit a control the extension must
//    never touch: a literal naming one, a click or submit call, a synthetic keyboard or
//    mouse event, ticking a box, navigating away.
//  - `watchInteractions` patches the DOM's own click/submit/dispatch/focus entry points and
//    records everything a run does, so the same rules are enforced on behaviour as well.
//
// Both are written to be run against a deliberately bad input as well as the real code, so
// the tests can show they would fail.
//
// What each net guarantees: the scan is a tripwire for the ordinary spellings of a click, a
// submit, a tick and a navigation (property access, element access with a literal key,
// destructuring, `.call`/`Reflect`, assignment operators, `setAttribute` and
// `defineProperty`). It is not a proof: building a name out of pieces at run time is out of
// scope for static analysis, and navigation is checked statically only. The behavioural run
// (the watcher plus the trap checkboxes in the tests) is the net for obfuscated spellings of a
// click or a tick.

export interface SourceViolation {
  file: string;
  line: number;
  rule: string;
  text: string;
}

export interface ScanOptions {
  /** Functions (by name) in which a `.click()` is allowed: the one place the engine
   * activates an option of the country list it has just filtered. */
  allowClickIn?: readonly string[];
  /** Functions in which `.focus()` is allowed. */
  allowFocusIn?: readonly string[];
  /** Exact literal values that name the word without naming a control (a status label). */
  allowLiterals?: readonly string[];
}

const SYNTHETIC_EVENT_CLASSES = new Set([
  "KeyboardEvent",
  "MouseEvent",
  "PointerEvent",
  "SubmitEvent",
  "FocusEvent",
  "TouchEvent",
  "InputEvent",
  "WheelEvent",
]);
const PLAIN_EVENT_TYPES = new Set(["input", "change"]);
// Members that act on a page when called (click, submit) and members that tick a box or move
// the page when assigned.
const ACTING_MEMBERS = new Set(["click", "submit", "requestSubmit"]);
const MUTATING_MEMBERS = new Set(["checked", "indeterminate", "location", "href", "hash", "search"]);
const DANGEROUS_MEMBERS = new Set([...ACTING_MEMBERS, ...MUTATING_MEMBERS]);
// Names whose mere presence means the code is calling things by a route the scan cannot read.
const INDIRECTION_IDENTIFIERS = new Set(["Reflect", "eval", "Function"]);
const ASSIGNMENT_OPERATORS = new Set<ts.SyntaxKind>([
  ts.SyntaxKind.EqualsToken,
  ts.SyntaxKind.BarBarEqualsToken,
  ts.SyntaxKind.AmpersandAmpersandEqualsToken,
  ts.SyntaxKind.QuestionQuestionEqualsToken,
  ts.SyntaxKind.PlusEqualsToken,
]);

function enclosingFunctionName(node: ts.Node): string | null {
  for (let current: ts.Node | undefined = node.parent; current !== undefined; current = current.parent) {
    if (
      (ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current)) &&
      current.name !== undefined &&
      ts.isIdentifier(current.name)
    ) {
      return current.name.text;
    }
    if (
      ts.isVariableDeclaration(current) &&
      ts.isIdentifier(current.name) &&
      current.initializer !== undefined &&
      (ts.isArrowFunction(current.initializer) || ts.isFunctionExpression(current.initializer))
    ) {
      return current.name.text;
    }
  }
  return null;
}

export function scanSource(file: string, source: string, options: ScanOptions = {}): SourceViolation[] {
  const clickAllowedHere = (node: ts.Node): boolean => {
    const inside = enclosingFunctionName(node);
    return inside !== null && (options.allowClickIn ?? []).includes(inside);
  };
  const sourceFile = ts.createSourceFile(file, source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
  const found: SourceViolation[] = [];
  const report = (node: ts.Node, rule: string): void => {
    const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
    found.push({ file, line: line + 1, rule, text: node.getText(sourceFile).slice(0, 120) });
  };

  const visit = (node: ts.Node): void => {
    // Module specifiers are paths, not selectors.
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) return;

    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      if (textNamesForbiddenControl(node.text) && !(options.allowLiterals ?? []).includes(node.text)) {
        report(node, "literal-names-a-forbidden-control");
      }
    } else if (ts.isRegularExpressionLiteral(node)) {
      if (textNamesForbiddenControl(node.text)) report(node, "literal-names-a-forbidden-control");
    }

    if (ts.isPropertyAccessExpression(node)) {
      // Named wherever it appears, not only when called, so `b.click.call(b)` and
      // `Reflect.apply(b.click, ...)` are seen as well as `b.click()`.
      const name = node.name.text;
      if (name === "submit" || name === "requestSubmit") report(node, "submit");
      if (name === "click" && !clickAllowedHere(node)) report(node, "click");
    }

    if (
      ts.isElementAccessExpression(node) &&
      ts.isStringLiteralLike(node.argumentExpression) &&
      DANGEROUS_MEMBERS.has(node.argumentExpression.text)
    ) {
      report(node, "element-access-to-a-dangerous-member");
    }

    // `const { click } = button` and `{ checked }` pull the member out without naming it as a call.
    if (ts.isBindingElement(node)) {
      const member = node.propertyName ?? node.name;
      if ((ts.isIdentifier(member) || ts.isStringLiteralLike(member)) && DANGEROUS_MEMBERS.has(member.text)) {
        report(node, "destructures-a-dangerous-member");
      }
    }
    if (ts.isShorthandPropertyAssignment(node) && DANGEROUS_MEMBERS.has(node.name.text)) {
      report(node, "destructures-a-dangerous-member");
    }

    if (ts.isIdentifier(node) && INDIRECTION_IDENTIFIERS.has(node.text)) report(node, "indirect-call");
    if (ts.isPropertyAccessExpression(node) && node.name.text === "fromCharCode") report(node, "indirect-call");

    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)) {
      const method = node.expression.name.text;
      const inside = enclosingFunctionName(node);
      if (method === "focus" && !(inside !== null && (options.allowFocusIn ?? []).includes(inside))) {
        report(node, "focus");
      }
      if (method === "dispatchEvent") {
        const arg = node.arguments[0];
        const isPlainEvent =
          arg !== undefined &&
          ts.isNewExpression(arg) &&
          ts.isIdentifier(arg.expression) &&
          arg.expression.text === "Event" &&
          arg.arguments?.[0] !== undefined &&
          ts.isStringLiteralLike(arg.arguments[0]) &&
          PLAIN_EVENT_TYPES.has(arg.arguments[0].text);
        if (!isPlainEvent) report(node, "dispatchEvent-of-anything-but-input-or-change");
      }
      const receiver = node.expression.expression.getText(sourceFile);
      if (
        ["open", "assign", "replace", "reload", "back", "forward"].includes(method) &&
        /^(?:window\.)?(?:location|history)$|^window$/u.test(receiver)
      ) {
        report(node, "navigation");
      }
      // Ticking or defining a box's state by another route than assignment.
      if (["setAttribute", "toggleAttribute", "defineProperty", "defineProperties"].includes(method)) {
        if (node.arguments.slice(0, 2).some((a) => ts.isStringLiteralLike(a) && MUTATING_MEMBERS.has(a.text))) {
          report(node, "ticks-a-box");
        }
      }
      if (method === "assign" && receiver === "Object") {
        const patch = node.arguments[1];
        if (
          patch !== undefined &&
          ts.isObjectLiteralExpression(patch) &&
          patch.properties.some((p) => p.name !== undefined && MUTATING_MEMBERS.has(p.name.getText(sourceFile)))
        ) {
          report(node, "ticks-a-box");
        }
      }
    }

    if (ts.isNewExpression(node) && ts.isIdentifier(node.expression) && SYNTHETIC_EVENT_CLASSES.has(node.expression.text)) {
      report(node, "synthetic-keyboard-or-mouse-event");
    }

    // Assignment of any kind (`=`, `||=`, `&&=`, `??=`, `+=`) to a member that ticks a box or
    // moves the page, or to `location` itself, or through an element access with such a literal key.
    if (ts.isBinaryExpression(node) && ASSIGNMENT_OPERATORS.has(node.operatorToken.kind)) {
      const left = node.left;
      const target = ts.isPropertyAccessExpression(left)
        ? left.name.text
        : ts.isElementAccessExpression(left) && ts.isStringLiteralLike(left.argumentExpression)
          ? left.argumentExpression.text
          : ts.isIdentifier(left) && left.text === "location"
            ? "location"
            : null;
      if (target === "checked" || target === "indeterminate") report(node, "ticks-a-box");
      if (target !== null && ["location", "href", "hash", "search"].includes(target)) report(node, "navigation");
    }

    ts.forEachChild(node, visit);
  };
  visit(sourceFile);
  return found;
}

// ---- behaviour ---------------------------------------------------------------------------

export interface Interaction {
  kind: "click" | "submit" | "requestSubmit" | "dispatch" | "focus";
  target: string;
  detail: string;
}

function describeElement(target: unknown): string {
  if (!(target instanceof Element)) return String(target);
  const id = target.id !== "" ? `#${target.id}` : "";
  const name = target.getAttribute("name") !== null ? `[name=${target.getAttribute("name")}]` : "";
  const type = target.getAttribute("type") !== null ? `[type=${target.getAttribute("type")}]` : "";
  const role = target.getAttribute("role") !== null ? `[role=${target.getAttribute("role")}]` : "";
  return `${target.tagName.toLowerCase()}${id}${name}${type}${role}`;
}

export interface Watcher {
  interactions: Interaction[];
  /** Anything that is not one of the three things the engine may do. */
  violations: () => Interaction[];
  restore: () => void;
}

/**
 * Records every click, submit, synthetic event and focus the code under test makes through the
 * DOM's own entry points. What is allowed: a plain `input`/`change` event on a field; a click
 * on an option of a listbox (the country list); focus on the country combobox.
 */
export function watchInteractions(): Watcher {
  const interactions: Interaction[] = [];
  const originals = {
    click: HTMLElement.prototype.click,
    focus: HTMLElement.prototype.focus,
    submit: HTMLFormElement.prototype.submit,
    requestSubmit: HTMLFormElement.prototype.requestSubmit,
    dispatchEvent: EventTarget.prototype.dispatchEvent,
  };
  HTMLElement.prototype.click = function (this: HTMLElement) {
    interactions.push({ kind: "click", target: describeElement(this), detail: "" });
    return originals.click.call(this);
  };
  HTMLElement.prototype.focus = function (this: HTMLElement, options?: FocusOptions) {
    interactions.push({ kind: "focus", target: describeElement(this), detail: "" });
    return originals.focus.call(this, options);
  };
  HTMLFormElement.prototype.submit = function (this: HTMLFormElement) {
    interactions.push({ kind: "submit", target: describeElement(this), detail: "" });
  };
  HTMLFormElement.prototype.requestSubmit = function (this: HTMLFormElement) {
    interactions.push({ kind: "requestSubmit", target: describeElement(this), detail: "" });
  };
  EventTarget.prototype.dispatchEvent = function (this: EventTarget, event: Event) {
    interactions.push({
      kind: "dispatch",
      target: describeElement(this),
      detail: `${event.constructor.name}:${event.type}${"key" in event ? `:${String((event as KeyboardEvent).key)}` : ""}`,
    });
    return originals.dispatchEvent.call(this, event);
  };

  return {
    interactions,
    violations: () =>
      interactions.filter((i) => {
        // A click is allowed only on a plain layout element that is an option: a tag, an
        // optional id, and the role, with no type, name or href (a button or link dressed up
        // as an option is a violation, whatever role it claims).
        if (i.kind === "click") return !/^(?:div|li|span)(?:#[^[]*)?\[role=option\]$/u.test(i.target);
        if (i.kind === "submit" || i.kind === "requestSubmit") return true;
        if (i.kind === "focus") return i.target !== "input#country[type=text][role=combobox]";
        return !/^Event:(?:input|change)$/u.test(i.detail);
      }),
    restore: () => {
      HTMLElement.prototype.click = originals.click;
      HTMLElement.prototype.focus = originals.focus;
      HTMLFormElement.prototype.submit = originals.submit;
      HTMLFormElement.prototype.requestSubmit = originals.requestSubmit;
      EventTarget.prototype.dispatchEvent = originals.dispatchEvent;
    },
  };
}
