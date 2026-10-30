/**
 * Canonical type spelling, nullability, and the confirmation wrapper.
 *
 * This mirrors the value spellings the accepted Python adapter produces, so a
 * fact emitted here compares equal to its Python counterpart:
 *
 *   nullability    `T | null`, `T | undefined`, `x?: T`  ->  base + `?`
 *   canonical      a known alias is normalised, anything else kept verbatim
 *   wrapper        `Confirmation<T, "AUTHORITY">` -> authority, else undefined
 */

/** The port interfaces a typed-code class may declare in its heritage. */
export const PORT_INTERFACES: readonly string[] = ["WorkflowStepInput", "WorkflowStepOutput"];

/** The wrapper that carries a confirmation authority on a value. */
export const CONFIRMATION_WRAPPER = "Confirmation";

/**
 * Written type name -> canonical domain type. Mirrors `TYPE_ALIASES` in the
 * crosswalk, so a spelling this table does not know stays *as written* and
 * surfaces as a real divergence rather than being smoothed away.
 */
const TYPE_ALIASES: Readonly<Record<string, string>> = {
  ID: "string",
  String: "string",
  string: "string",
  str: "string",
  Text: "string",
  Int: "integer",
  int: "integer",
  Integer: "integer",
  Float: "number",
  float: "number",
  number: "number",
  Number: "number",
  boolean: "boolean",
  Boolean: "boolean",
  bool: "boolean",
  Date: "datetime",
  DateTime: "datetime",
  datetime: "datetime",
  Decimal: "decimal",
  decimal: "decimal",
  Duration: "duration",
  URI: "uri",
  UUID: "uuid",
  uuid: "uuid",
  Confidential: "confidential",
  any: "any",
  unknown: "unknown",
  never: "never",
  object: "object",
  void: "void",
};

/** Split `text` on `separator`, but only at bracket depth zero. */
function splitTopLevel(text: string, separator: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const character of text) {
    if (character === "<" || character === "[" || character === "(" || character === "{") {
      depth += 1;
    } else if (character === ">" || character === "]" || character === ")" || character === "}") {
      depth -= 1;
    }
    if (character === separator && depth === 0) {
      parts.push(current);
      current = "";
      continue;
    }
    current += character;
  }
  parts.push(current);
  return parts;
}

/** True when `text` is a plain (possibly dotted) type name, no delimiters. */
function isPlainName(text: string): boolean {
  return /^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/.test(text);
}

/** The name inside a `Wrapper<...>` / `Wrapper[...]` head, or undefined. */
function wrapperInner(text: string, head: string): string | undefined {
  const angle = `${head}<`;
  const square = `${head}[`;
  if (text.startsWith(angle) && text.endsWith(">")) {
    return text.slice(angle.length, -1);
  }
  if (text.startsWith(square) && text.endsWith("]")) {
    return text.slice(square.length, -1);
  }
  return undefined;
}

/**
 * Normalise a written type spelling onto a canonical domain type.
 *
 * Nullability is read from `T | null`, `T | undefined`, `Optional<T>` and a
 * trailing `?`, and is preserved as a trailing `?`. An unknown base name is
 * preserved verbatim.
 */
export function canonicalType(written: string): string {
  let text = written.trim();
  let nullable = false;
  let isList = false;

  if (text.endsWith("?")) {
    nullable = true;
    text = text.slice(0, -1).trim();
  }
  if (text.endsWith("!")) {
    text = text.slice(0, -1).trim();
  }

  // A union whose members include null/undefined is nullable; the base is the
  // first member, exactly as the Python adapter reads `X | None`.
  const members = splitTopLevel(text, "|");
  if (members.length > 1) {
    const rest = members.slice(1).map((member) => member.trim());
    if (rest.some((member) => member === "null" || member === "undefined" || member === "None")) {
      nullable = true;
    }
    text = members[0]!.trim();
  }

  const optionalInner = wrapperInner(text, "Optional");
  if (optionalInner !== undefined) {
    nullable = true;
    text = optionalInner.trim();
  }

  if (text.endsWith("[]")) {
    isList = true;
    text = text.slice(0, -2).trim();
  } else {
    const arrayInner = wrapperInner(text, "Array");
    if (arrayInner !== undefined) {
      isList = true;
      text = arrayInner.trim();
    }
  }

  let canonical = TYPE_ALIASES[text];
  if (canonical === undefined) {
    canonical = TYPE_ALIASES[text.toLowerCase()];
  }
  if (canonical === undefined && isPlainName(text) && text.includes(".")) {
    const tail = text.slice(text.lastIndexOf(".") + 1);
    canonical = TYPE_ALIASES[tail] ?? TYPE_ALIASES[tail.toLowerCase()];
  }
  if (canonical === undefined) {
    canonical = text;
  }

  return `${canonical}${isList ? "[]" : ""}${nullable ? "?" : ""}`;
}

/**
 * The confirmation authority declared by a `Confirmation<T, "AUTHORITY">`
 * wrapper, read from the written syntax so it is a declared literal and never a
 * value inferred by running the application. `undefined` when not wrapped.
 */
export function confirmationOf(written: string | undefined): string | undefined {
  if (written === undefined) {
    return undefined;
  }
  const text = written.trim();
  const inner = wrapperInner(text, CONFIRMATION_WRAPPER);
  if (inner === undefined) {
    return undefined;
  }
  const parts = splitTopLevel(inner, ",");
  if (parts.length < 2) {
    return undefined;
  }
  const authority = parts[1]!.trim();
  const quoted =
    (authority.startsWith('"') && authority.endsWith('"')) ||
    (authority.startsWith("'") && authority.endsWith("'"));
  return quoted ? authority.slice(1, -1) : undefined;
}

/** `Confirmation<T, "AUTHORITY">` -> `T`; any other spelling unchanged. */
export function unwrappedType(written: string | undefined): string | undefined {
  if (written === undefined) {
    return undefined;
  }
  const text = written.trim();
  const inner = wrapperInner(text, CONFIRMATION_WRAPPER);
  if (inner === undefined) {
    return written;
  }
  const parts = splitTopLevel(inner, ",");
  return parts[0]?.trim() ?? written;
}

/**
 * One parameter's emitted spelling: `name:Type`, with a `?` suffix when the
 * parameter is optional or its type is nullable.
 */
export function paramShape(name: string, written: string | undefined, optional: boolean): string {
  let canonical = written === undefined ? "any" : canonicalType(written);
  const nullable = optional || canonical.endsWith("?");
  if (nullable && !canonical.endsWith("?")) {
    canonical = `${canonical}?`;
  }
  const base = canonical.endsWith("?") ? canonical.slice(0, -1) : canonical;
  return `${name}:${base}${nullable ? "?" : ""}`;
}