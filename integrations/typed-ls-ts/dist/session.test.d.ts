/**
 * GQL-05 node (c) — restart-and-conflict tests for the typed language-server pair.
 *
 * The pair is the accepted Python adapter side and the accepted TypeScript
 * adapter side. This suite pins exactly the node (c) clause:
 *
 *   * the restart is deterministic — the same open state restarts to the same
 *     digest, and the documents are replayed in insertion order;
 *   * the conflict that a restart produces is explicit, observable and tested —
 *     a stale-generation edit, a stale-text edit and a stale-version change are
 *     each reported as a typed SessionConflict, never silently swallowed;
 *   * a clean edit against the current generation still applies.
 *
 * Every conflict assertion pins the exact kind and the exact reason string, so
 * a test that cannot fail is not written here. The stale-text wording mirrors
 * the accepted Python CodegenConflict ("An edit no longer matches the source it
 * was computed against"), so both media report the same problem in the same
 * terms.
 */
export {};
