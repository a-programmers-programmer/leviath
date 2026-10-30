/**
 * GQL-05 node (c): the typed language-server session and its restart.
 *
 * The pair is two endpoints over the accepted adapter side:
 *
 *   typescript  the accepted node (a) adapter in `./adapter.js`, in process
 *   python      a port the caller supplies, because the Python tree
 *               (`python/leviath_typed_ls/`) is read-only to this lane
 *
 * A restart is a first-class, deterministic operation, not a side effect of
 * dropping state. `restart()` advances the generation, replays every open
 * document in insertion order, and returns a digest that is a pure function of
 * the replayed state. Two restarts of the same documents therefore produce the
 * same digest; a restart after an edit produces a different one.
 *
 * The conflict behaviour is explicit, observable and never swallowed. An edit
 * carries the generation it was computed at. If the session restarted in
 * between, the edit is stale, and this module says so with a
 * `SessionConflict` rather than writing the source. The stale-text wording
 * mirrors the accepted Python `CodegenConflict` ("an edit no longer matches the
 * source it was computed against"), so both media report the same problem in the
 * same terms. Every conflict is appended to the session's conflict log and is
 * cleared only by an explicit `clearConflicts()`.
 */
import { type TypedExtraction } from "./adapter.js";
/** The two languages of the pair. */
export type Language = "python" | "typescript";
/** One endpoint's extraction capability. */
export interface AdapterEndpoint {
    extract(source: string, path: string): TypedExtraction;
}
/** The pair of endpoints a session runs over. */
export interface Endpoints {
    typescript: AdapterEndpoint;
    python: AdapterEndpoint;
}
/** One applicable replacement in a typed-code artifact. */
export interface CodeEdit {
    path: string;
    /** 1-based line number. */
    line: number;
    /** 0-based inclusive start column. */
    col_start: number;
    /** 0-based exclusive end column. */
    col_end: number;
    old_text: string;
    new_text: string;
    symbol_key: string;
    attribute: string;
}
/** Why a restart, an edit or a change was refused. */
export type ConflictKind = "stale-generation" | "stale-text" | "stale-version" | "unknown-endpoint";
/** One explicit, locatable conflict. Never a silent swallow. */
export interface SessionConflict {
    kind: ConflictKind;
    path: string;
    /** 1-based line, or 0 when the conflict is not tied to a line. */
    line: number;
    col_start: number;
    col_end: number;
    /** What the caller's operation assumed. */
    expected: string;
    /** What the session actually holds. */
    observed: string;
    reason: string;
    /** What the caller must do to make the operation admissible. */
    obligation: string;
}
/** The outcome of one edit or change: applied, or refused with conflicts. */
export interface SessionResult {
    applied: boolean;
    conflicts: SessionConflict[];
}
/** The outcome of one restart. */
export interface RestartReceipt {
    /** The generation after the restart. Always advances by one. */
    generation: number;
    /** The documents replayed, in insertion order. */
    replayed: string[];
    /**
     * SHA-256 over the replayed state, so two restarts of the same state produce
     * the same bytes and a restart after an edit does not.
     */
    digest: string;
}
/**
 * The typed language-server pair session.
 *
 * State is code-owned: documents, their versions, the generation counter, the
 * conflict log and the restart digest are all plain bytes this class owns. No
 * model is consulted, and nothing is inferred.
 */
export declare class TypedLsSession {
    private readonly endpoints;
    private readonly documents;
    private readonly conflictLog;
    private currentGeneration;
    private lastDigest;
    constructor(endpoints?: Partial<Endpoints>);
    /** The current generation: the number of restarts so far. */
    get generation(): number;
    /** The digest of the last restart, or "" before the first restart. */
    get digest(): string;
    /** The conflict log. Retained until `clearConflicts()` is called. */
    get conflicts(): readonly SessionConflict[];
    /** The open document URIs, in insertion order. */
    get uris(): string[];
    /** The text of `uri`, or "" when it is not open. */
    text(uri: string): string;
    /** The version of `uri`, or 0 when it is not open. */
    version(uri: string): number;
    /** Open a document. The generation the text was established in is recorded. */
    open(uri: string, text: string, version?: number): SessionResult;
    /**
     * Change a document's text.
     *
     * A version that does not advance is a `stale-version` conflict: the caller is
     * trying to overwrite newer text with older text, which is the write a
     * language server must refuse rather than perform.
     */
    change(uri: string, text: string, version: number): SessionResult;
    /**
     * Restart the session, deterministically.
     *
     * The generation advances by one, every open document is replayed in insertion
     * order, and the replay digest is a pure function of the replayed state. The
     * conflict log is NOT cleared: a restart must not silently hide a conflict.
     */
    restart(): RestartReceipt;
    /**
     * Apply one edit, verifying the generation it was computed at and the span it
     * claims to replace.
     *
     * Both checks are refusals, never silent writes: a stale edit leaves the
     * document byte-identical and records an explicit conflict.
     */
    applyEdit(uri: string, edit: CodeEdit, computedAtGeneration: number): SessionResult;
    /** Extract one open document on one endpoint, through that endpoint's adapter. */
    extract(language: Language, uri: string): TypedExtraction;
    /** Empty the conflict log. The ONLY way a conflict disappears. */
    clearConflicts(): void;
    /** Record one conflict and return the refusing result. Never exotic. */
    private refuse;
}
