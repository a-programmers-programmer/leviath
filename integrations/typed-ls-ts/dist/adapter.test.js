/**
 * GQL-05 node (a) — tests for the TypeScript typed-code adapter.
 *
 * One test per emitted attribute: callable.params, callable.returns,
 * callable.signature, callable.port, callable.confirmation (field form),
 * callable.confirmation.<param> (parameter form), nullability, and one
 * unsupported-construct case that asserts the extraction is blocked.
 *
 * A test that cannot fail is worthless, so each assertion pins an exact value
 * string rather than a shape. The expected strings are copied from the accepted
 * Python adapter's output for the equivalent source (directive 0193).
 */
import { strict as assert } from "node:assert";
import { test } from "node:test";
import { extractTyped } from "./adapter.js";
const FIXTURE = [
    "export interface WorkflowStepInput {", //                                                    1
    "  simpleEnglishDescription: string;", //                                                     2
    "}", //                                                                                       3
    "", //                                                                                        4
    "export class OAuthRefreshTokenInput implements WorkflowStepInput {", //                      5
    "  simpleEnglishDescription: string;", //                                                     6
    '  token: Confirmation<Confidential, "STEP_RUNNER">;', //                                      7
    "  expiresAt: Date;", //                                                                      8
    "  scope: string[];", //                                                                      9
    "}", //                                                                                      10
    "", //                                                                                       11
    "export class Dispatcher {", //                                                              12
    "  dispatch(workflow: WorkflowDefinition, dryRun?: boolean): WorkflowRun {", //              13
    "    return workflow;", //                                                                   14
    "  }", //                                                                                    15
    "  find(id: string): WorkflowRun | undefined { return undefined; }", //                      16
    '  gated(token: Confirmation<Confidential, "HUMAN_GATE">, note: string | null): void {}', // 17
    "}", //                                                                                      18
].join("\n");
function index(facts) {
    const map = new Map();
    for (const fact of facts) {
        map.set(`${fact.symbol_key}|${fact.attribute}`, fact);
    }
    return map;
}
function value(facts, key, attribute) {
    const fact = index(facts).get(`${key}|${attribute}`);
    assert.ok(fact, `expected fact ${key} / ${attribute} to be emitted`);
    return fact.value;
}
test("callable.params are comma-joined name:Type pieces", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    // dryRun is an optional parameter, so it is nullable: base + "?".
    assert.equal(value(projection.facts, "Dispatcher.dispatch", "callable.params"), "workflow:WorkflowDefinition,dryRun:boolean?");
    // A single parameter, no nullability.
    assert.equal(value(projection.facts, "Dispatcher.find", "callable.params"), "id:string");
});
test("callable.returns is the canonical return type", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    assert.equal(value(projection.facts, "Dispatcher.dispatch", "callable.returns"), "WorkflowRun");
    assert.equal(value(projection.facts, "Dispatcher.gated", "callable.returns"), "void");
});
test("callable.signature is (params)->returns", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    assert.equal(value(projection.facts, "Dispatcher.dispatch", "callable.signature"), "(workflow:WorkflowDefinition,dryRun:boolean?)->WorkflowRun");
});
test("callable.port is emitted for a class declaring a port in its heritage", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    assert.equal(value(projection.facts, "OAuthRefreshTokenInput", "callable.port"), "WorkflowStepInput");
    // A class that declares no port emits no port fact.
    assert.equal(index(projection.facts).has("Dispatcher|callable.port"), false);
});
test("callable.confirmation is emitted for a confirmation-wrapped class field", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    const fact = index(projection.facts).get("OAuthRefreshTokenInput.token|callable.confirmation");
    assert.ok(fact, "expected the wrapped field to emit callable.confirmation");
    assert.equal(fact.value, "by=STEP_RUNNER");
    // Source points at the edited field line, and the end column follows the rule
    // col_end = col_start + max(len(last dot-segment of symbol_key), 1) -> 2 + 5.
    assert.equal(fact.source.path, "app/ports.ts");
    assert.equal(fact.source.line, 7);
    assert.equal(fact.source.startCol, 2);
    assert.equal(fact.source.endCol, 7);
});
test("callable.confirmation.<param> is emitted for a wrapped parameter", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    assert.equal(value(projection.facts, "Dispatcher.gated", "callable.confirmation.token"), "by=HUMAN_GATE");
    // The wrapped parameter keeps its written spelling in params.
    assert.equal(value(projection.facts, "Dispatcher.gated", "callable.params"), 'token:Confirmation<Confidential, "HUMAN_GATE">,note:string?');
});
test("nullability: T | null, T | undefined and x?: T become base + ?", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    // Optional parameter `dryRun?: boolean`.
    assert.equal(value(projection.facts, "Dispatcher.dispatch", "callable.params"), "workflow:WorkflowDefinition,dryRun:boolean?");
    // Union with null on a parameter -> `note:string?`.
    assert.equal(value(projection.facts, "Dispatcher.gated", "callable.params"), 'token:Confirmation<Confidential, "HUMAN_GATE">,note:string?');
    // Union with undefined on a return -> `WorkflowRun?`.
    assert.equal(value(projection.facts, "Dispatcher.find", "callable.returns"), "WorkflowRun?");
});
test("source spans match the Python adapter's rule", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    const facts = index(projection.facts);
    // callable.* facts point at the callable declaration; name is the last
    // dot-segment of the symbol key -> len("dispatch") === 8, col 2 -> 10.
    const params = facts.get("Dispatcher.dispatch|callable.params");
    assert.ok(params);
    assert.equal(params.source.line, 13);
    assert.equal(params.source.startCol, 2);
    assert.equal(params.source.endCol, 10);
    // callable.port points at the class declaration -> len(className) === 22.
    const port = facts.get("OAuthRefreshTokenInput|callable.port");
    assert.ok(port);
    assert.equal(port.source.line, 5);
    assert.equal(port.source.startCol, 0);
    assert.equal(port.source.endCol, 22);
});
test("every emitted fact is projection 'code' with confidence 1.0", () => {
    const { projection } = extractTyped(FIXTURE, "app/ports.ts");
    assert.ok(projection.facts.length > 0);
    for (const fact of projection.facts) {
        assert.equal(fact.projection, "code");
        assert.equal(fact.confidence, 1.0);
        assert.equal(fact.source.path, "app/ports.ts");
        assert.ok(fact.source.line >= 1);
        assert.ok(fact.source.endCol >= fact.source.startCol);
    }
});
const DYNAMIC = [
    'import * as vendored from "./vendor.js";', //                         1
    'export * from "./other.js";', //                                      2
    "", //                                                                 3
    "export function build(name: string): string {", //                    4
    "  return eval(name);", //                                             5
    "}", //                                                                6
    "", //                                                                 7
    "export class Registry {", //                                          8
    "  [nameFor()]: string;", //                                           9
    "}", //                                                               10
    "", //                                                                11
    'Object.defineProperty(Registry.prototype, "made", { value: 1 });', // 12
].join("\n");
test("unsupported dynamic constructs are reported and the extraction is blocked", () => {
    const extraction = extractTyped(DYNAMIC, "app/dynamic.ts");
    const constructs = extraction.unsupported.map((finding) => finding.construct);
    assert.ok(constructs.some((c) => c.includes("import *")));
    assert.ok(constructs.some((c) => c.includes("export *")));
    assert.ok(constructs.some((c) => c.includes("eval(")));
    assert.ok(constructs.some((c) => c.includes("computed member name")));
    assert.ok(constructs.some((c) => c.includes("defineProperty(")));
    for (const finding of extraction.unsupported) {
        assert.equal(finding.path, "app/dynamic.ts");
        assert.ok(finding.line >= 1);
        assert.ok(finding.reason.length > 0);
        assert.ok(finding.obligation.length > 0);
    }
    assert.equal(extraction.blocked, true);
});
test("markers inside comments and string literals do not block the extraction", () => {
    const benign = [
        '// eval(name) and Object.defineProperty(x, "y", {}) are mentioned here', // 1
        'export const note = "eval(x)";', //                                       2
        "export function ok(a: number): number { return a; }", //                  3
    ].join("\n");
    const extraction = extractTyped(benign, "app/benign.ts");
    assert.deepEqual(extraction.unsupported, []);
    assert.equal(extraction.blocked, false);
    assert.equal(value(extraction.projection.facts, "ok", "callable.params"), "a:number");
    assert.equal(value(extraction.projection.facts, "ok", "callable.returns"), "number");
});
//# sourceMappingURL=adapter.test.js.map