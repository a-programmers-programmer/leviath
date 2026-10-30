/**
 * GQL-05 node (b) — tests for the two-server session and cross-language
 * navigation.
 *
 * The session runs two language servers side by side: the accepted Python
 * typed-code adapter reached over a line-JSON child process, and the accepted
 * TypeScript adapter reached in-process. Navigation goes from a position in one
 * language to the position of the *same symbol key* in the other, using the
 * shared symbol-key namespace both adapters already emit.
 *
 * Every expected line and column below was observed from the accepted adapters
 * before this test was written (directive 0193); none is invented. LSP
 * positions are 0-based on both axes.
 */
import { strict as assert } from "node:assert";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { TwoServerSession } from "./session.js";
const PY_URI = "file:///app/ports.py";
const TS_URI = "file:///app/ports.ts";
/** Assert a span is present and return it, so the assertions below read plain. */
function span(value) {
    assert.ok(value !== null, "expected a span, got null");
    return value;
}
async function fixturePath(name) {
    return new URL(`../py/fixtures/${name}`, import.meta.url).pathname;
}
async function openSession() {
    const session = await TwoServerSession.start();
    await session.open("python", PY_URI, await readFile(await fixturePath("ports.py"), "utf8"));
    await session.open("typescript", TS_URI, await readFile(await fixturePath("ports.ts"), "utf8"));
    return session;
}
test("the session runs both servers side by side", async () => {
    const session = await openSession();
    try {
        const both = session.servers();
        assert.equal(both.length, 2, "one server per language");
        const languages = both.map((s) => s.language).sort();
        assert.deepEqual(languages, ["python", "typescript"]);
        // The Python side is a real child process; the TypeScript side is in-process.
        const python = both.find((s) => s.language === "python");
        const typescript = both.find((s) => s.language === "typescript");
        assert.equal(python.transport, "child-process");
        assert.ok(python.pid !== undefined && python.pid > 0, "the Python server has a pid");
        assert.equal(typescript.transport, "in-process");
    }
    finally {
        await session.close();
    }
});
test("navigates from a TypeScript field position to the Python declaration", async () => {
    const session = await openSession();
    try {
        // `token` on line 2 (1-based) of ports.ts, columns 2..7.
        const result = await session.navigate({
            uri: TS_URI,
            position: { line: 1, character: 3 },
        });
        assert.equal(result.reason, "resolved");
        assert.equal(result.blocked, false);
        assert.equal(span(result.origin).symbolKey, "OAuthRefreshTokenInput.token");
        assert.equal(span(result.origin).language, "typescript");
        assert.equal(span(result.target).language, "python");
        assert.equal(span(result.target).symbolKey, "OAuthRefreshTokenInput.token");
        assert.equal(span(result.target).path, "app/ports.py");
        // Python field `token` is line 5 (1-based), columns 4..9.
        assert.deepEqual(span(result.target).start, { line: 4, character: 4 });
        assert.deepEqual(span(result.target).end, { line: 4, character: 9 });
    }
    finally {
        await session.close();
    }
});
test("navigates from a Python field position back to the TypeScript declaration", async () => {
    const session = await openSession();
    try {
        const result = await session.navigate({
            uri: PY_URI,
            position: { line: 4, character: 5 },
        });
        assert.equal(result.reason, "resolved");
        assert.equal(span(result.origin).symbolKey, "OAuthRefreshTokenInput.token");
        assert.equal(span(result.target).language, "typescript");
        // TypeScript field `token` is line 2 (1-based), columns 2..7.
        assert.deepEqual(span(result.target).start, { line: 1, character: 2 });
        assert.deepEqual(span(result.target).end, { line: 1, character: 7 });
    }
    finally {
        await session.close();
    }
});
test("navigates a method across languages", async () => {
    const session = await openSession();
    try {
        const toPython = await session.navigate({ uri: TS_URI, position: { line: 3, character: 4 } });
        assert.equal(span(toPython.origin).symbolKey, "OAuthRefreshTokenInput.refresh");
        assert.equal(span(toPython.target).language, "python");
        // Python `refresh` is line 7 (1-based), columns 4..11.
        assert.deepEqual(span(toPython.target).start, { line: 6, character: 4 });
        assert.deepEqual(span(toPython.target).end, { line: 6, character: 11 });
        const toTypescript = await session.navigate({
            uri: PY_URI,
            position: { line: 6, character: 5 },
        });
        assert.equal(span(toTypescript.origin).symbolKey, "OAuthRefreshTokenInput.refresh");
        assert.equal(span(toTypescript.target).language, "typescript");
        // TypeScript `refresh` is line 4 (1-based), columns 2..9.
        assert.deepEqual(span(toTypescript.target).start, { line: 3, character: 2 });
        assert.deepEqual(span(toTypescript.target).end, { line: 3, character: 9 });
    }
    finally {
        await session.close();
    }
});
test("navigates a class across languages through its port declaration", async () => {
    const session = await openSession();
    try {
        const toPython = await session.navigate({ uri: TS_URI, position: { line: 0, character: 4 } });
        assert.equal(span(toPython.origin).symbolKey, "OAuthRefreshTokenInput");
        assert.equal(span(toPython.target).language, "python");
        // Python class is line 4 (1-based), columns 0..22.
        assert.deepEqual(span(toPython.target).start, { line: 3, character: 0 });
        assert.deepEqual(span(toPython.target).end, { line: 3, character: 22 });
        const toTypescript = await session.navigate({
            uri: PY_URI,
            position: { line: 3, character: 4 },
        });
        assert.equal(span(toTypescript.origin).symbolKey, "OAuthRefreshTokenInput");
        assert.equal(span(toTypescript.target).language, "typescript");
        assert.deepEqual(span(toTypescript.target).start, { line: 0, character: 0 });
        assert.deepEqual(span(toTypescript.target).end, { line: 0, character: 22 });
    }
    finally {
        await session.close();
    }
});
test("reports a symbol that exists in only one language instead of inventing one", async () => {
    const session = await openSession();
    try {
        // `OnlyInPython` is at Python line 11 (1-based).
        const fromPython = await session.navigate({
            uri: PY_URI,
            position: { line: 10, character: 3 },
        });
        assert.equal(span(fromPython.origin).symbolKey, "OnlyInPython");
        assert.equal(fromPython.reason, "absent-in-other-language");
        assert.equal(fromPython.target, null);
        // `OnlyInTypescript.declared` is at TypeScript line 10 (1-based).
        const fromTypescript = await session.navigate({
            uri: TS_URI,
            position: { line: 9, character: 4 },
        });
        assert.equal(span(fromTypescript.origin).symbolKey, "OnlyInTypescript.declared");
        assert.equal(fromTypescript.reason, "absent-in-other-language");
        assert.equal(fromTypescript.target, null);
    }
    finally {
        await session.close();
    }
});
test("says so when no symbol is at the position", async () => {
    const session = await openSession();
    try {
        // A blank line in ports.py.
        const result = await session.navigate({ uri: PY_URI, position: { line: 1, character: 0 } });
        assert.equal(result.reason, "no-symbol-at-position");
        assert.equal(result.target, null);
    }
    finally {
        await session.close();
    }
});
test("refuses to guess when an extraction is blocked", async () => {
    const session = await openSession();
    try {
        // A wildcard import hides which names are declared: the TypeScript adapter
        // blocks the extraction. Navigation must not pretend the symbol is absent.
        const blockedUri = "file:///app/blocked.ts";
        await session.open("typescript", blockedUri, 'import * as ns from "./ns.js";\nexport class Blocked {\n  declared(name: string): void {}\n}\n');
        const result = await session.navigate({ uri: blockedUri, position: { line: 2, character: 4 } });
        assert.equal(result.blocked, true);
        assert.equal(result.reason, "blocked");
        assert.equal(result.target, null);
    }
    finally {
        await session.close();
    }
});
test("the join table lists the symbols present in both languages", async () => {
    const session = await openSession();
    try {
        const table = await session.joinTable();
        const shared = table
            .filter((row) => row.python && row.typescript)
            .map((row) => row.symbolKey)
            .sort();
        assert.deepEqual(shared, [
            "OAuthRefreshTokenInput",
            "OAuthRefreshTokenInput.refresh",
            "OAuthRefreshTokenInput.token",
        ]);
        const pythonOnly = table.filter((row) => row.python && !row.typescript).map((r) => r.symbolKey);
        assert.deepEqual(pythonOnly, ["OnlyInPython"]);
        const typescriptOnly = table
            .filter((row) => row.typescript && !row.python)
            .map((r) => r.symbolKey)
            .sort();
        assert.deepEqual(typescriptOnly, ["OnlyInTypescript.declared"]);
    }
    finally {
        await session.close();
    }
});
//# sourceMappingURL=navigation.test.js.map