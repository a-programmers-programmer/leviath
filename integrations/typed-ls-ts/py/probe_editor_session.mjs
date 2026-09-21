/**
 * Evidence probe for GQL-05 node (d): print what the *editor session* observes.
 *
 * The assertions in `src/editor-session.test.ts` are the test. This probe is the
 * independent observation behind them: it runs the same session, over the same
 * two real protocol servers, and prints what came back. It writes nothing and
 * changes nothing.
 *
 *   node py/probe_editor_session.mjs
 *
 * Prints: both servers with their transports; the conflict the diverged rename
 * raises; the diagnostics each language server returns; one navigation in each
 * direction; and the restart receipt plus the conflict afterwards.
 */

import { readFile } from "node:fs/promises";

import { EditorSession } from "../dist/editor-session.js";

const SCHEMA_URI = "file:///app/schema.graphql";
const PY_URI = "file:///app/ports.py";
const TS_URI = "file:///app/ports.ts";

const BASE_SDL = "type WorkflowStep {\n  token: String\n}\n";
const BASE_PY = "class WorkflowStep:\n    token: str\n";
const DIVERGE_SDL = "type WorkflowStep {\n  accessToken: String\n}\n";
const DIVERGE_PY = "class WorkflowStep:\n    refreshToken: str\n";

const here = new URL(".", import.meta.url).pathname;
const pyText = await readFile(`${here}fixtures/ports.py`, "utf8");
const tsText = await readFile(`${here}fixtures/ports.ts`, "utf8");

const session = await EditorSession.start({
  baseline: { schema: BASE_SDL, code: BASE_PY },
  code: { path: "model.py", text: DIVERGE_PY },
});

console.log("=== servers ===");
for (const server of session.servers()) {
  console.log(`  ${server.name}: transport=${server.transport} pid=${server.pid ?? "-"}`);
}

console.log("=== diagnostics: TypeScript document (ports.ts) ===");
await session.openDocument({ uri: TS_URI, languageId: "typescript", version: 1, text: tsText });
console.log(`  items=${JSON.stringify(await session.diagnostics(TS_URI))}`);

console.log("=== diagnostics: GraphQL document (diverged rename) ===");
await session.openDocument({ uri: SCHEMA_URI, languageId: "graphql", version: 1, text: DIVERGE_SDL });
const items = await session.diagnostics(SCHEMA_URI);
console.log(`  items=${items.length}`);
for (const item of items) {
  console.log(`  code=${item.code} source=${item.source} kind=${item.data?.kind} conflictCode=${item.data?.conflictCode}`);
  console.log(`  symbolKey=${item.data?.symbolKey} schemaValue=${item.data?.schemaValue} otherValue=${item.data?.otherValue}`);
  console.log(`  requiresConfirmation=${item.data?.requiresConfirmation} options=${JSON.stringify(item.data?.options)}`);
  console.log(`  range=${JSON.stringify(item.range)}`);
}

console.log("=== navigation: Python <-> TypeScript ===");
await session.openDocument({ uri: PY_URI, languageId: "python", version: 1, text: pyText });
console.log("  TS -> PY: " + JSON.stringify(await session.navigate(TS_URI, { line: 1, character: 3 })));
console.log("  PY -> TS: " + JSON.stringify(await session.navigate(PY_URI, { line: 4, character: 5 })));

console.log("=== restart ===");
const receipt = await session.restart();
console.log("  " + JSON.stringify(receipt));
const after = await session.diagnostics(SCHEMA_URI);
console.log(`  conflicts after restart=${after.length} code=${after[0]?.code ?? "-"}`);
console.log(`  same conflict fields=${JSON.stringify(after.map((d) => d.data?.symbolKey))}`);

console.log("=== baseline ===");
console.log("  " + JSON.stringify(await session.baseline()));

await session.close();
console.log("=== session closed ===");
