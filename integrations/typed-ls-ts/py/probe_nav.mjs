/**
 * Evidence probe for GQL-05 node (b): print what the session actually observes.
 *
 * This writes nothing and changes nothing. It starts both servers, prints the
 * spans each adapter reports for the same two source files, and prints one
 * navigation in each direction. The output *is* the evidence for the expected
 * values the test asserts.
 *
 *   node py/probe_nav.mjs
 */

import { readFile } from "node:fs/promises";
import { TwoServerSession } from "../dist/session.js";

const PY_URI = "file:///app/ports.py";
const TS_URI = "file:///app/ports.ts";

const here = new URL(".", import.meta.url).pathname;
const pyText = await readFile(`${here}fixtures/ports.py`, "utf8");
const tsText = await readFile(`${here}fixtures/ports.ts`, "utf8");

const session = await TwoServerSession.start();
await session.open("python", PY_URI, pyText);
await session.open("typescript", TS_URI, tsText);

console.log("=== servers ===");
for (const server of session.servers()) {
  console.log(`  ${server.language}: transport=${server.transport} pid=${server.pid ?? "-"}`);
}

console.log("=== join table (observed) ===");
for (const row of await session.joinTable()) {
  console.log(
    `  ${row.symbolKey.padEnd(34)} python=${String(row.python).padEnd(5)} typescript=${row.typescript}`,
  );
}

console.log("=== navigation TS -> Python (token, line 2 col 3) ===");
console.log("  " + JSON.stringify(await session.navigate({ uri: TS_URI, position: { line: 1, character: 3 } })));

console.log("=== navigation Python -> TS (token, line 5 col 5) ===");
console.log("  " + JSON.stringify(await session.navigate({ uri: PY_URI, position: { line: 4, character: 5 } })));

console.log("=== navigation Python-only symbol ===");
console.log("  " + JSON.stringify(await session.navigate({ uri: PY_URI, position: { line: 10, character: 3 } })));

await session.close();
console.log("=== session closed ===");