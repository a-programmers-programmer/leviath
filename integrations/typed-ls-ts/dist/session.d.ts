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
/** The two languages this session runs side by side. */
export type Language = "python" | "typescript";
/** Where one server is reached from. */
export interface ServerInfo {
    language: Language;
    transport: "child-process" | "in-process";
    /** Present for the child process, absent for the in-process server. */
    pid?: number;
}
/** An LSP position: 0-based on both axes. */
export interface Position {
    line: number;
    character: number;
}
/** One symbol's declaration span, in LSP coordinates. */
export interface SymbolSpan {
    language: Language;
    symbolKey: string;
    path: string;
    start: Position;
    end: Position;
}
/** Why a navigation returned what it returned. */
export type NavigationReason = "resolved" | "absent-in-other-language" | "no-symbol-at-position" | "blocked";
/** One cross-language navigation answer. */
export interface NavigationResult {
    reason: NavigationReason;
    /** True when the origin extraction was blocked by an unsupported construct. */
    blocked: boolean;
    origin: SymbolSpan | null;
    target: SymbolSpan | null;
}
/** One row of the join table: which languages declare this symbol key. */
export interface JoinRow {
    symbolKey: string;
    python: boolean;
    typescript: boolean;
}
/** One fact, in the shape both adapters can produce. */
interface RawFact {
    k: string;
    a: string;
    v: string;
    p: string;
    line: number;
    cs: number;
    ce: number;
}
/** `file:///app/ports.py` -> `app/ports.py`; anything else unchanged. */
export declare function pathFromUri(uri: string): string;
/** One declaration span per symbol key: the earliest by (line, column). */
export declare function declarationSpans(language: Language, path: string, facts: readonly RawFact[]): Map<string, SymbolSpan>;
/** The symbol whose span contains `position`, or `null` when none does. */
export declare function symbolAt(spans: ReadonlyMap<string, SymbolSpan>, position: Position): SymbolSpan | null;
/**
 * A two-server session: both adapters, one join table, one navigation call.
 *
 * Nothing here imports, executes or evaluates an analysed artifact. The Python
 * side is a child process running the accepted adapter; the TypeScript side is
 * the accepted in-process adapter.
 */
export declare class TwoServerSession {
    private readonly documents;
    private child;
    private reader;
    private queue;
    private constructor();
    /** Start both servers: the Python child process and the in-process adapter. */
    static start(): Promise<TwoServerSession>;
    /** Spawn the accepted Python adapter behind the line-JSON driver. */
    private spawnPython;
    /** The two servers this session runs, with where each one lives. */
    servers(): ServerInfo[];
    /** Register an open document under the server that owns its language. */
    open(language: Language, uri: string, text: string): void;
    /** The most recently opened document for one language, if any. */
    private documentFor;
    /** One request to the Python child; replies are matched in order. */
    private ask;
    /** Extract one document, from the server that owns its language. */
    private extract;
    /** Spans for the most recently opened document of one language. */
    private spansFor;
    /** Every symbol key either adapter declares, and where. */
    joinTable(): Promise<JoinRow[]>;
    /**
     * Resolve a position in one language to the same symbol in the other.
     *
     * A blocked extraction refuses (`reason: "blocked"`). A position with no
     * declaring symbol says so. A symbol the other language does not declare is
     * reported as absent, never invented.
     */
    navigate(request: {
        uri: string;
        position: Position;
    }): Promise<NavigationResult>;
    /** Stop both servers. */
    close(): Promise<void>;
}
export {};
