// A DOM-free way to look inside, and press, what a hook-free React component
// returns -- for tests in a package that has no DOM environment.
//
// `renderToStaticMarkup` (used elsewhere) shows what a component LOOKS like but
// cannot click anything, so "pressing this button calls that handler" and "this
// button ignores presses while busy" were untestable, and the panel's guards
// could be deleted with every test still green. A component that is a pure
// function of its props (`HiringSignalsPanelView`, the cards) can simply be
// CALLED; what it returns is a tree of elements whose `onClick` props are real
// functions. `expand` turns that tree into plain host elements (calling any
// hook-free child components on the way), and `press` calls an `onClick`.
//
// Deliberately tiny and dumb: it is not a renderer. A child that uses a hook
// (react-router's `Link`) cannot be called outside a render; it is left as an
// opaque `component` leaf instead of throwing, so a test that never touches it
// is unaffected.

import { isValidElement, type ReactElement, type ReactNode } from "react";

export interface HostElement {
  kind: "host";
  type: string;
  key: string | null;
  props: Record<string, unknown>;
  children: TreeNode[];
}

export interface OpaqueComponent {
  kind: "component";
  name: string;
}

export type TreeNode = HostElement | OpaqueComponent | string;

function isPlainProps(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function expand(node: ReactNode): TreeNode[] {
  if (node === null || node === undefined || typeof node === "boolean") return [];
  if (typeof node === "string" || typeof node === "number") return [String(node)];
  if (Array.isArray(node)) return node.flatMap((child) => expand(child as ReactNode));
  if (!isValidElement(node)) return [];

  const element = node as ReactElement<Record<string, unknown>>;
  const props = isPlainProps(element.props) ? element.props : {};
  const type: unknown = element.type;

  if (typeof type === "string") {
    return [
      {
        kind: "host",
        type,
        key: element.key,
        props,
        children: expand(props.children as ReactNode),
      },
    ];
  }
  if (typeof type === "function") {
    try {
      return expand((type as (p: Record<string, unknown>) => ReactNode)(props));
    } catch {
      return [{ kind: "component", name: (type as { name?: string }).name ?? "Anonymous" }];
    }
  }
  // A fragment (a symbol type) has no element of its own: its children stand in.
  return expand(props.children as ReactNode);
}

export function isHost(node: TreeNode): node is HostElement {
  return typeof node === "object" && node.kind === "host";
}

export function findAll(
  nodes: readonly TreeNode[],
  predicate: (element: HostElement) => boolean,
): HostElement[] {
  const found: HostElement[] = [];
  for (const node of nodes) {
    if (!isHost(node)) continue;
    if (predicate(node)) found.push(node);
    found.push(...findAll(node.children, predicate));
  }
  return found;
}

export function textOf(node: TreeNode): string {
  if (typeof node === "string") return node;
  if (node.kind === "component") return "";
  return node.children.map(textOf).join("");
}

// The buttons whose visible text is exactly `label` (whitespace collapsed).
export function buttonsLabelled(nodes: readonly TreeNode[], label: string): HostElement[] {
  return findAll(
    nodes,
    (el) => el.type === "button" && textOf(el).replace(/\s+/g, " ").trim() === label,
  );
}

export function onlyButton(nodes: readonly TreeNode[], label: string): HostElement {
  const matches = buttonsLabelled(nodes, label);
  if (matches.length !== 1) {
    throw new Error(`expected exactly one "${label}" button, found ${matches.length}`);
  }
  return matches[0];
}

// Calls the element's onClick, as a click on it would.
export function press(element: HostElement): void {
  const handler = element.props.onClick;
  if (typeof handler !== "function") {
    throw new Error(`<${element.type}> has no onClick to press`);
  }
  (handler as (event: unknown) => void)({});
}

export function prop(element: HostElement, name: string): unknown {
  return element.props[name];
}
