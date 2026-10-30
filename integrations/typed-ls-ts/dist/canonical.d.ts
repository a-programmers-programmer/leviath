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
export declare const PORT_INTERFACES: readonly string[];
/** The wrapper that carries a confirmation authority on a value. */
export declare const CONFIRMATION_WRAPPER = "Confirmation";
/**
 * Normalise a written type spelling onto a canonical domain type.
 *
 * Nullability is read from `T | null`, `T | undefined`, `Optional<T>` and a
 * trailing `?`, and is preserved as a trailing `?`. An unknown base name is
 * preserved verbatim.
 */
export declare function canonicalType(written: string): string;
/**
 * The confirmation authority declared by a `Confirmation<T, "AUTHORITY">`
 * wrapper, read from the written syntax so it is a declared literal and never a
 * value inferred by running the application. `undefined` when not wrapped.
 */
export declare function confirmationOf(written: string | undefined): string | undefined;
/** `Confirmation<T, "AUTHORITY">` -> `T`; any other spelling unchanged. */
export declare function unwrappedType(written: string | undefined): string | undefined;
/**
 * One parameter's emitted spelling: `name:Type`, with a `?` suffix when the
 * parameter is optional or its type is nullable.
 */
export declare function paramShape(name: string, written: string | undefined, optional: boolean): string;
