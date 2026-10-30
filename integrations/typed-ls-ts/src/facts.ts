/**
 * The fact shape, matching `python/leviath_reconcile/model.py` exactly.
 *
 * A `Fact` is one (subject, attribute, value) proposition plus provenance and
 * confidence. `SourceRef` is the span in the analysed file that produced it.
 * Both are the same shape the accepted Python adapter emits, so a fact from
 * either adapter is directly comparable.
 */

/** A span in one projection's source text. */
export interface SourceRef {
  path: string;
  /** 1-based line number. */
  line: number;
  /** 0-based inclusive start column. */
  startCol: number;
  /** 0-based exclusive end column. */
  endCol: number;
}

/** One proposition about one symbol, tagged with its projection and source. */
export interface Fact {
  /** Stable identity of the subject. */
  symbol_key: string;
  /** Always "code" for everything this adapter emits. */
  projection: string;
  /** Namespaced attribute, e.g. "callable.params". */
  attribute: string;
  /** Always a string. */
  value: string;
  source: SourceRef;
  /** 1.0 unless provisional. */
  confidence: number;
}

/** The last dot-segment of a symbol key: the name the span is sized from. */
export function lastSegment(symbolKey: string): string {
  const parts = symbolKey.split(".");
  return parts[parts.length - 1] ?? symbolKey;
}

/**
 * Build a fact, setting `source` by the same rule the Python `_fact` helper
 * uses: `SourceRef(path, line, col, col + max(len(name), 1))`.
 */
export function fact(
  symbolKey: string,
  attribute: string,
  value: string,
  path: string,
  line: number,
  col: number,
): Fact {
  const name = lastSegment(symbolKey);
  return {
    symbol_key: symbolKey,
    projection: "code",
    attribute,
    value,
    source: { path, line, startCol: col, endCol: col + Math.max(name.length, 1) },
    confidence: 1.0,
  };
}