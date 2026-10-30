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
import { strict as assert } from "node:assert";
import { test } from "node:test";
import { TypedLsSession, } from "./session.js";
const SRC_V1 = [
    "export class Dispatcher {", //                                  1
    "  dispatch(workflow: WorkflowDefinition): WorkflowRun {", //   2
    "    return workflow;", //                                       3
    "  }", //                                                        4
    "}", //                                                          5
].join("\n");
const SRC_V2 = SRC_V1.replace("workflow: WorkflowDefinition", "workflow: WorkflowDefinition | null");
/** A change that genuinely rewrites the span the edit claims to replace. */
const SRC_V3 = SRC_V1.replace("workflow: WorkflowDefinition", "payloads: WorkflowDefinition");
/** An edit against SRC_V1: the workflow parameter becomes nullable. */
function editForV1() {
    return {
        path: "app/ports.ts",
        line: 2,
        col_start: 11,
        col_end: 39,
        old_text: "workflow: WorkflowDefinition",
        new_text: "workflow: WorkflowDefinition | null",
        symbol_key: "Dispatcher.dispatch",
        attribute: "callable.params",
    };
}
function kinds(conflicts) {
    return conflicts.map((conflict) => conflict.kind);
}
test("restart is deterministic: same state restarts to the same digest", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    session.open("file:///app/other.ts", SRC_V2, 1);
    const first = session.restart();
    const second = session.restart();
    // The generation advances on every restart, so a caller can always tell
    // "before" from "after".
    assert.equal(first.generation, 1);
    assert.equal(second.generation, 2);
    // The digest is a pure function of the replayed state, so two restarts of the
    // same open documents produce the same bytes.
    assert.equal(first.digest, second.digest);
    assert.match(first.digest, /^[0-9a-f]{64}$/);
    // The replay order is insertion order, deterministically.
    assert.deepEqual(first.replayed, ["file:///app/ports.ts", "file:///app/other.ts"]);
    assert.deepEqual(second.replayed, first.replayed);
});
test("restart replays the open documents and keeps their text", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    session.restart();
    const text = session.text("file:///app/ports.ts");
    assert.equal(text, SRC_V1);
});
test("a stale-generation edit is a conflict, not a silent write", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    // The edit is computed against generation 0 (before any restart).
    const edit = editForV1();
    const computedAt = session.generation;
    // The session restarts: the edit is now stale.
    session.restart();
    const result = session.applyEdit("file:///app/ports.ts", edit, computedAt);
    assert.equal(result.applied, false);
    const conflict = result.conflicts[0];
    assert.equal(conflict.kind, "stale-generation");
    assert.equal(conflict.expected, String(computedAt));
    assert.equal(conflict.observed, String(session.generation));
    assert.equal(conflict.reason, "the edit was computed before a restart");
    assert.ok(conflict.obligation.length > 0);
    // The document is untouched: a conflict never writes.
    assert.equal(session.text("file:///app/ports.ts"), SRC_V1);
});
test("a stale-text edit is a conflict, mirroring the Python CodegenConflict", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    const edit = editForV1();
    // The document changed out from under the edit: the parameter was renamed.
    session.change("file:///app/ports.ts", SRC_V3, 2);
    const result = session.applyEdit("file:///app/ports.ts", edit, session.generation);
    assert.equal(result.applied, false);
    const conflict = result.conflicts[0];
    assert.equal(conflict.kind, "stale-text");
    assert.equal(conflict.expected, edit.old_text);
    assert.equal(conflict.observed, "payloads: WorkflowDefinition");
    assert.equal(conflict.reason, "an edit no longer matches the source it was computed against");
    assert.ok(conflict.obligation.length > 0);
    assert.equal(conflict.line, 2);
    assert.equal(conflict.col_start, 11);
    assert.equal(conflict.col_end, 39);
    // The document is untouched.
    assert.equal(session.text("file:///app/ports.ts"), SRC_V3);
});
test("a stale-version change is a conflict, not a silent overwrite", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 5);
    // A change that regresses the version is refused.
    const result = session.change("file:///app/ports.ts", SRC_V2, 3);
    assert.equal(result.applied, false);
    const conflict = result.conflicts[0];
    assert.equal(conflict.kind, "stale-version");
    assert.equal(conflict.expected, "> 5");
    assert.equal(conflict.observed, "3");
    assert.equal(conflict.reason, "the change version does not advance the document");
    // The document keeps its text and its version.
    assert.equal(session.text("file:///app/ports.ts"), SRC_V1);
    assert.equal(session.version("file:///app/ports.ts"), 5);
});
test("an edit against an unknown endpoint is a conflict, not a crash", () => {
    const session = new TypedLsSession();
    const result = session.applyEdit("file:///app/missing.ts", editForV1(), 0);
    assert.equal(result.applied, false);
    assert.equal(result.conflicts[0].kind, "unknown-endpoint");
});
test("a clean edit against the current generation applies", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    const result = session.applyEdit("file:///app/ports.ts", editForV1(), session.generation);
    assert.equal(result.applied, true);
    assert.deepEqual(result.conflicts, []);
    assert.equal(session.text("file:///app/ports.ts"), SRC_V2);
});
test("conflicts are observable and are only cleared explicitly", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    session.restart();
    session.applyEdit("file:///app/ports.ts", editForV1(), 0);
    // The conflict is retained on the session, so a caller can always see it.
    assert.equal(session.conflicts.length, 1);
    assert.equal(kinds(session.conflicts)[0], "stale-generation");
    // A restart does NOT silently drop the conflict log.
    session.restart();
    assert.equal(session.conflicts.length, 1);
    // Only an explicit clear empties it.
    session.clearConflicts();
    assert.deepEqual(session.conflicts, []);
});
test("the pair extracts on both endpoints through the accepted adapters", () => {
    const session = new TypedLsSession();
    session.open("file:///app/ports.ts", SRC_V1, 1);
    // The TypeScript endpoint delegates to the accepted node (a) adapter.
    const ts = session.extract("typescript", "file:///app/ports.ts");
    assert.equal(ts.projection.facts.length > 0, true);
    // The Python endpoint is a port supplied by the caller, because the Python
    // tree is read-only to this lane.
    const session2 = new TypedLsSession({
        python: {
            extract: (source, path) => ({
                projection: { name: "code", facts: [] },
                unsupported: [],
                warnings: [],
                mode: "python-ast-static",
                blocked: false,
            }),
        },
    });
    session2.open("file:///app/ports.py", "class A:\n    pass\n", 1);
    const py = session2.extract("python", "file:///app/ports.py");
    assert.equal(py.mode, "python-ast-static");
});
//# sourceMappingURL=session.test.js.map