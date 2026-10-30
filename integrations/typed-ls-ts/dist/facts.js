/**
 * The fact shape, matching `python/leviath_reconcile/model.py` exactly.
 *
 * A `Fact` is one (subject, attribute, value) proposition plus provenance and
 * confidence. `SourceRef` is the span in the analysed file that produced it.
 * Both are the same shape the accepted Python adapter emits, so a fact from
 * either adapter is directly comparable.
 */
/** The last dot-segment of a symbol key: the name the span is sized from. */
export function lastSegment(symbolKey) {
    const parts = symbolKey.split(".");
    return parts[parts.length - 1] ?? symbolKey;
}
/**
 * Build a fact, setting `source` by the same rule the Python `_fact` helper
 * uses: `SourceRef(path, line, col, col + max(len(name), 1))`.
 */
export function fact(symbolKey, attribute, value, path, line, col) {
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
//# sourceMappingURL=facts.js.map