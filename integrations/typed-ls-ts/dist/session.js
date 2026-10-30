/**
 * GQL-05 node (b): two language servers in one session, plus cross-language
 * navigation.
 *
 * Both servers are the *accepted* adapters, reached the way each one is
 * actually implemented:
 *
 *   python      the accepted `leviath_typed_ls.typed_code_adapter`, reached
 *               over a line-JSON child process (`py/dump_facts.py`), because
 *               that adapter is Python and must not be re-implemented here;
 *   typescript  the accepted `extractTyped` adapter, called in-process.
 *
 * Cross-language navigation needs the source span that produced each fact, and
 * both adapters already emit spans in one shared symbol-key namespace. So the
 * join is a table lookup on `symbolKey`, never a guess: a symbol present in both
 * languages resolves to the other language's span, a symbol present in one
 * resolves to `null` with a stated reason, and a blocked extraction refuses
 * instead of reporting a symbol as absent.
 *
 * The child process is started with `PYTHONDONTWRITEBYTECODE=1`, so reading the
 * accepted checkout never writes bytecode into it.
 */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { extractTyped } from "./adapter.js";
/** The accepted Python package root, read-only. */
const RECONCILE_ROOT = process.env.LEVIATH_RECONCILE_PATH ?? "/data/work/graphql-realization/gql-05/repo/python";
/** `file:///app/ports.py` -> `app/ports.py`; anything else unchanged. */
export function pathFromUri(uri) {
    if (!uri.startsWith("file://")) {
        return uri;
    }
    return decodeURIComponent(uri.slice("file://".length)).replace(/^\/+/, "");
}
/** One span covers a position when the line matches and the column is inside. */
function contains(span, position) {
    return (span.start.line === position.line &&
        position.character >= span.start.character &&
        position.character <= span.end.character);
}
/** Span width, used to pick the innermost symbol at a position. */
function width(span) {
    return span.end.character - span.start.character;
}
/** One declaration span per symbol key: the earliest by (line, column). */
export function declarationSpans(language, path, facts) {
    const byKey = new Map();
    for (const raw of facts) {
        const span = {
            language,
            symbolKey: raw.k,
            path: raw.p || path,
            start: { line: Math.max(0, raw.line - 1), character: raw.cs },
            end: { line: Math.max(0, raw.line - 1), character: raw.ce },
        };
        const current = byKey.get(span.symbolKey);
        if (current === undefined ||
            span.start.line < current.start.line ||
            (span.start.line === current.start.line && span.start.character < current.start.character)) {
            byKey.set(span.symbolKey, span);
        }
    }
    return byKey;
}
/** The symbol whose span contains `position`, or `null` when none does. */
export function symbolAt(spans, position) {
    let best = null;
    for (const span of spans.values()) {
        if (!contains(span, position)) {
            continue;
        }
        if (best === null ||
            width(span) < width(best) ||
            (width(span) === width(best) && span.start.character < best.start.character)) {
            best = span;
        }
    }
    return best;
}
/**
 * A two-server session: both adapters, one join table, one navigation call.
 *
 * Nothing here imports, executes or evaluates an analysed artifact. The Python
 * side is a child process running the accepted adapter; the TypeScript side is
 * the accepted in-process adapter.
 */
export class TwoServerSession {
    documents = new Map();
    child = null;
    reader = null;
    queue = Promise.resolve();
    constructor() { }
    /** Start both servers: the Python child process and the in-process adapter. */
    static async start() {
        const session = new TwoServerSession();
        session.spawnPython();
        return session;
    }
    /** Spawn the accepted Python adapter behind the line-JSON driver. */
    spawnPython() {
        const script = new URL("../py/dump_facts.py", import.meta.url).pathname;
        const child = spawn(process.env.PYTHON ?? "python3", [script], {
            cwd: new URL("..", import.meta.url).pathname,
            env: {
                ...process.env,
                // Reading the accepted checkout must never write bytecode into it.
                PYTHONDONTWRITEBYTECODE: "1",
                PYTHONPATH: RECONCILE_ROOT,
            },
            stdio: ["pipe", "pipe", "pipe"],
        });
        this.child = child;
        child.stderr.resume();
        this.reader = createInterface({ input: child.stdout, crlfDelay: Infinity });
    }
    /** The two servers this session runs, with where each one lives. */
    servers() {
        const python = {
            language: "python",
            transport: "child-process",
            pid: this.child?.pid,
        };
        const typescript = { language: "typescript", transport: "in-process" };
        return [python, typescript];
    }
    /** Register an open document under the server that owns its language. */
    open(language, uri, text) {
        this.documents.set(uri, { language, uri, text });
    }
    /** The most recently opened document for one language, if any. */
    documentFor(language) {
        let found;
        for (const document of this.documents.values()) {
            if (document.language === language) {
                found = document;
            }
        }
        return found;
    }
    /** One request to the Python child; replies are matched in order. */
    ask(request) {
        const run = this.queue.then(() => new Promise((resolve, reject) => {
            const child = this.child;
            const reader = this.reader;
            if (child === null || reader === null) {
                reject(new Error("python server is not running"));
                return;
            }
            const onLine = (line) => {
                reader.off("line", onLine);
                try {
                    const reply = JSON.parse(line);
                    if (reply.error !== undefined) {
                        reject(new Error(reply.error));
                        return;
                    }
                    resolve({ facts: reply.facts ?? [], blocked: reply.blocked === true });
                }
                catch (error) {
                    reject(error instanceof Error ? error : new Error(String(error)));
                }
            };
            reader.on("line", onLine);
            child.stdin.write(`${JSON.stringify(request)}\n`);
        }));
        this.queue = run.catch(() => undefined);
        return run;
    }
    /** Extract one document, from the server that owns its language. */
    async extract(document) {
        const path = pathFromUri(document.uri);
        if (document.language === "python") {
            return this.ask({ source: document.text, path });
        }
        const extraction = extractTyped(document.text, path);
        return {
            facts: extraction.projection.facts.map((fact) => ({
                k: fact.symbol_key,
                a: fact.attribute,
                v: fact.value,
                p: fact.source.path,
                line: fact.source.line,
                cs: fact.source.startCol,
                ce: fact.source.endCol,
            })),
            blocked: extraction.blocked,
        };
    }
    /** Spans for the most recently opened document of one language. */
    async spansFor(language) {
        const document = this.documentFor(language);
        if (document === undefined) {
            return new Map();
        }
        const extraction = await this.extract(document);
        return declarationSpans(language, pathFromUri(document.uri), extraction.facts);
    }
    /** Every symbol key either adapter declares, and where. */
    async joinTable() {
        const [python, typescript] = await Promise.all([
            this.spansFor("python"),
            this.spansFor("typescript"),
        ]);
        const keys = new Set([...python.keys(), ...typescript.keys()]);
        return [...keys].sort().map((symbolKey) => ({
            symbolKey,
            python: python.has(symbolKey),
            typescript: typescript.has(symbolKey),
        }));
    }
    /**
     * Resolve a position in one language to the same symbol in the other.
     *
     * A blocked extraction refuses (`reason: "blocked"`). A position with no
     * declaring symbol says so. A symbol the other language does not declare is
     * reported as absent, never invented.
     */
    async navigate(request) {
        const document = this.documents.get(request.uri);
        if (document === undefined) {
            return { reason: "no-symbol-at-position", blocked: false, origin: null, target: null };
        }
        const own = await this.extract(document);
        if (own.blocked) {
            return { reason: "blocked", blocked: true, origin: null, target: null };
        }
        const ownSpans = declarationSpans(document.language, pathFromUri(document.uri), own.facts);
        const origin = symbolAt(ownSpans, request.position);
        if (origin === null) {
            return { reason: "no-symbol-at-position", blocked: false, origin: null, target: null };
        }
        const other = document.language === "python" ? "typescript" : "python";
        const otherSpans = await this.spansFor(other);
        const target = otherSpans.get(origin.symbolKey) ?? null;
        if (target === null) {
            return { reason: "absent-in-other-language", blocked: false, origin, target: null };
        }
        return { reason: "resolved", blocked: false, origin, target };
    }
    /** Stop both servers. */
    async close() {
        const child = this.child;
        this.child = null;
        if (this.reader !== null) {
            this.reader.close();
            this.reader = null;
        }
        if (child !== null) {
            child.stdin.end();
            await new Promise((resolve) => {
                const done = () => resolve();
                child.once("exit", done);
                child.once("close", done);
                child.kill();
            });
        }
    }
}
//# sourceMappingURL=session.js.map