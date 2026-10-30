/**
 * TypeScript typed-code adapter: declared callable ports and metadata, read
 * statically.
 *
 * This is the TypeScript counterpart of
 * `leviath_typed_ls.typed_code_adapter`. It emits the **same fact shape** and
 * the **same value spellings** for the same declaration, so a fact emitted here
 * is directly comparable with a fact emitted by the Python adapter:
 *
 *   callable.params                     comma-joined `name:Type` pieces
 *   callable.returns                    canonical return type
 *   callable.signature                  `(params)->returns`
 *   callable.port                       the declared port interface name
 *   callable.confirmation.<paramName>   `by=<authority>` for a wrapped parameter
 *   callable.confirmation               `by=<authority>` for a wrapped class field
 *
 * Reading is entirely static. `typescript@7.0.2` here is the native port: it
 * exposes a scanner but no in-process text->AST parser, and the high-level API
 * spawns a second server process, which this node must not do. So the token
 * stream is read with `createScanner` and walked by a small recursive-descent
 * reader. The analysed file is never imported, evaluated or executed.
 */
import { type Fact } from "./facts.js";
export type { Fact } from "./facts.js";
export type { SourceRef } from "./facts.js";
import { type UnsupportedConstruct } from "./unsupported.js";
/** The projection envelope: the facts, and the projection they belong to. */
export interface Projection {
    name: string;
    facts: Fact[];
}
/** One typed-code artifact's extraction. */
export interface TypedExtraction {
    projection: Projection;
    unsupported: UnsupportedConstruct[];
    warnings: string[];
    mode: string;
    /** True when an obligation is outstanding: the projection is inadmissible. */
    blocked: boolean;
}
/**
 * Extract one typed-TypeScript artifact statically.
 *
 * Never imports, never executes, never evaluates the artifact. A construct that
 * cannot be read statically becomes an `UnsupportedConstruct`, and the
 * extraction is then `blocked`.
 */
export declare function extractTyped(source: string, path?: string): TypedExtraction;
