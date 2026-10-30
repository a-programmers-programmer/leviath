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

import { createHash } from "node:crypto";

import { extractTyped, type TypedExtraction } from "./adapter.js";

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
export type ConflictKind =
  | "stale-generation"
  | "stale-text"
  | "stale-version"
  | "unknown-endpoint";

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

/** One open document. */
interface Document {
  uri: string;
  text: string;
  version: number;
  /** The generation this document's text was established in. */
  generation: number;
}

/** The default TypeScript endpoint: the accepted node (a) adapter, in process. */
const TYPESCRIPT_ENDPOINT: AdapterEndpoint = {
  extract: (source: string, path: string): TypedExtraction => extractTyped(source, path),
};

/**
 * The default Python endpoint. The Python adapter lives in
 * `python/leviath_typed_ls/typed_code_adapter.py`, which is read-only to this
 * lane, so the Python side is a declared port: a caller that owns the Python
 * tree injects it. Until then, this endpoint refuses with an obligation rather
 * than pretending it extracted something it did not read.
 */
const PYTHON_ENDPOINT: AdapterEndpoint = {
  extract: (_source: string, path: string): TypedExtraction => ({
    projection: { name: "code", facts: [] },
    unsupported: [
      {
        construct: "python endpoint not supplied",
        path,
        line: 1,
        reason: "the Python adapter is read-only to this lane",
        obligation: "inject a python endpoint that owns the Python tree",
      },
    ],
    warnings: [],
    mode: "python-endpoint-unbound",
    blocked: true,
  }),
};

/** True when `text` starts at `col_start` with the recorded `old_text`. */
function spanText(text: string, line: number, colStart: number, colEnd: number): string {
  const lines = text.split("\n");
  if (line < 1 || line > lines.length) {
    return "";
  }
  return lines[line - 1]!.slice(colStart, colEnd);
}

/** Replace one span, verifying the span still holds `old_text`. */
function replaceSpan(text: string, edit: CodeEdit): string {
  const lines = text.split("\n");
  const index = edit.line - 1;
  const line = lines[index]!;
  lines[index] = line.slice(0, edit.col_start) + edit.new_text + line.slice(edit.col_end);
  return lines.join("\n");
}

/**
 * The typed language-server pair session.
 *
 * State is code-owned: documents, their versions, the generation counter, the
 * conflict log and the restart digest are all plain bytes this class owns. No
 * model is consulted, and nothing is inferred.
 */
export class TypedLsSession {
  private readonly endpoints: Endpoints;
  private readonly documents = new Map<string, Document>();
  private readonly conflictLog: SessionConflict[] = [];
  private currentGeneration = 0;
  private lastDigest = "";

  constructor(endpoints: Partial<Endpoints> = {}) {
    this.endpoints = {
      typescript: endpoints.typescript ?? TYPESCRIPT_ENDPOINT,
      python: endpoints.python ?? PYTHON_ENDPOINT,
    };
  }

  /** The current generation: the number of restarts so far. */
  get generation(): number {
    return this.currentGeneration;
  }

  /** The digest of the last restart, or "" before the first restart. */
  get digest(): string {
    return this.lastDigest;
  }

  /** The conflict log. Retained until `clearConflicts()` is called. */
  get conflicts(): readonly SessionConflict[] {
    return this.conflictLog;
  }

  /** The open document URIs, in insertion order. */
  get uris(): string[] {
    return [...this.documents.keys()];
  }

  /** The text of `uri`, or "" when it is not open. */
  text(uri: string): string {
    return this.documents.get(uri)?.text ?? "";
  }

  /** The version of `uri`, or 0 when it is not open. */
  version(uri: string): number {
    return this.documents.get(uri)?.version ?? 0;
  }

  /** Open a document. The generation the text was established in is recorded. */
  open(uri: string, text: string, version = 1): SessionResult {
    this.documents.delete(uri);
    this.documents.set(uri, {
      uri,
      text,
      version,
      generation: this.currentGeneration,
    });
    return { applied: true, conflicts: [] };
  }

  /**
   * Change a document's text.
   *
   * A version that does not advance is a `stale-version` conflict: the caller is
   * trying to overwrite newer text with older text, which is the write a
   * language server must refuse rather than perform.
   */
  change(uri: string, text: string, version: number): SessionResult {
    const document = this.documents.get(uri);
    if (document === undefined) {
      return this.refuse({
        kind: "unknown-endpoint",
        path: uri,
        line: 0,
        col_start: 0,
        col_end: 0,
        expected: "an open document",
        observed: "not open",
        reason: "the document is not open in this session",
        obligation: "open the document before changing it",
      });
    }
    if (!(version > document.version)) {
      return this.refuse({
        kind: "stale-version",
        path: uri,
        line: 0,
        col_start: 0,
        col_end: 0,
        expected: `> ${document.version}`,
        observed: String(version),
        reason: "the change version does not advance the document",
        obligation: `send a version greater than ${document.version}`,
      });
    }
    document.text = text;
    document.version = version;
    document.generation = this.currentGeneration;
    return { applied: true, conflicts: [] };
  }

  /**
   * Restart the session, deterministically.
   *
   * The generation advances by one, every open document is replayed in insertion
   * order, and the replay digest is a pure function of the replayed state. The
   * conflict log is NOT cleared: a restart must not silently hide a conflict.
   */
  restart(): RestartReceipt {
    this.currentGeneration += 1;
    const replayed = [...this.documents.keys()];
    this.lastDigest = digestOf(replayed.map((uri) => this.documents.get(uri)!));
    for (const uri of replayed) {
      this.documents.get(uri)!.generation = this.currentGeneration;
    }
    return { generation: this.currentGeneration, replayed, digest: this.lastDigest };
  }

  /**
   * Apply one edit, verifying the generation it was computed at and the span it
   * claims to replace.
   *
   * Both checks are refusals, never silent writes: a stale edit leaves the
   * document byte-identical and records an explicit conflict.
   */
  applyEdit(uri: string, edit: CodeEdit, computedAtGeneration: number): SessionResult {
    const document = this.documents.get(uri);
    if (document === undefined) {
      return this.refuse({
        kind: "unknown-endpoint",
        path: uri,
        line: edit.line,
        col_start: edit.col_start,
        col_end: edit.col_end,
        expected: "an open document",
        observed: "not open",
        reason: "the document is not open in this session",
        obligation: "open the document before editing it",
      });
    }
    if (computedAtGeneration !== document.generation) {
      return this.refuse({
        kind: "stale-generation",
        path: uri,
        line: edit.line,
        col_start: edit.col_start,
        col_end: edit.col_end,
        expected: String(computedAtGeneration),
        observed: String(document.generation),
        reason: "the edit was computed before a restart",
        obligation: `recompute the edit at generation ${document.generation}`,
      });
    }
    const observed = spanText(document.text, edit.line, edit.col_start, edit.col_end);
    if (observed !== edit.old_text) {
      return this.refuse({
        kind: "stale-text",
        path: uri,
        line: edit.line,
        col_start: edit.col_start,
        col_end: edit.col_end,
        expected: edit.old_text,
        observed,
        reason: "an edit no longer matches the source it was computed against",
        obligation: "recompute the edit against the current text",
      });
    }
    document.text = replaceSpan(document.text, edit);
    return { applied: true, conflicts: [] };
  }

  /** Extract one open document on one endpoint, through that endpoint's adapter. */
  extract(language: Language, uri: string): TypedExtraction {
    const document = this.documents.get(uri);
    const text = document?.text ?? "";
    return this.endpoints[language].extract(text, uri);
  }

  /** Empty the conflict log. The ONLY way a conflict disappears. */
  clearConflicts(): void {
    this.conflictLog.length = 0;
  }

  /** Record one conflict and return the refusing result. Never exotic. */
  private refuse(conflict: SessionConflict): SessionResult {
    this.conflictLog.push(conflict);
    return { applied: false, conflicts: [conflict] };
  }
}

/**
 * The restart digest: SHA-256 over every document's URI, version and text, in
 * the given (insertion) order.
 *
 * The digest is deliberately over the *replayed state* and not over the
 * generation counter, so two restarts of the same documents produce the same
 * bytes and a restart after an edit does not.
 */
function digestOf(documents: readonly Document[]): string {
  const hash = createHash("sha256");
  for (const document of documents) {
    hash.update(`uri:${document.uri}\n`);
    hash.update(`version:${document.version}\n`);
    hash.update(`text:${document.text}\n`);
  }
  return hash.digest("hex");
}