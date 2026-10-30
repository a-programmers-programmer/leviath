/**
 * Explicit, locatable findings for constructs the static reader will not guess
 * about.
 *
 * This mirrors `UnsupportedConstruct` in the Python adapter
 * (`leviath_typed_ls.typed_code_adapter`): the same five fields, the same
 * "obligation" wording, and the same rule that any finding marks the extraction
 * `blocked`. A construct is never silently skipped and never guessed.
 */
import { type Token } from "./reader.js";
/** One dynamic construct the static reader will not guess about. */
export interface UnsupportedConstruct {
    construct: string;
    path: string;
    /** 1-based line number. */
    line: number;
    reason: string;
    obligation: string;
}
/** A text marker, its reason, and the obligation it places on the author. */
interface Marker {
    marker: string;
    reason: string;
    obligation: string;
}
/**
 * Language features that make a declaration dynamic. Each name is the Python
 * adapter's counterpart where one exists, so the two adapters report the same
 * problem in the same terms.
 */
export declare const DYNAMIC_MARKERS: readonly Marker[];
/** Computed member name: `[nameFor()]: string`. */
export declare const COMPUTED_NAME_CONSTRUCT = "computed member name";
export declare const COMPUTED_NAME_REASON = "the member name is computed at run time";
export declare const COMPUTED_NAME_OBLIGATION = "declare the member with a literal name";
/** Wildcard import: `import * as ns from "..."`. */
export declare const WILDCARD_IMPORT_CONSTRUCT = "import *";
export declare const WILDCARD_IMPORT_REASON = "wildcard import hides which names are declared";
export declare const WILDCARD_IMPORT_OBLIGATION = "import the names explicitly";
/** Wildcard re-export: `export * from "..."`. */
export declare const WILDCARD_EXPORT_CONSTRUCT = "export *";
export declare const WILDCARD_EXPORT_REASON = "wildcard re-export hides which names are declared";
export declare const WILDCARD_EXPORT_OBLIGATION = "export the names explicitly";
/**
 * Find every dynamic text marker, skipping comments and string literals.
 *
 * Skipping those spans matters: a marker mentioned in a comment or inside a
 * string is data, not a construct, and blocking on it would be a false alarm.
 */
export declare function findTextMarkers(source: string, path: string, lineAt: (offset: number) => number, skipSpans: ReadonlyArray<readonly [number, number]>): UnsupportedConstruct[];
/** The token kinds `findStructuralFindings` needs. */
export interface StructuralTokenKinds {
    import: number;
    export: number;
    star: number;
}
/**
 * Find structural markers that cannot be found by text alone.
 *
 * `import *` is reported whether or not it binds a name, and `export *` is
 * reported separately, because they hide which names are declared in two
 * different directions.
 */
export declare function findStructuralFindings(tokens: readonly Token[], path: string, kinds: StructuralTokenKinds): UnsupportedConstruct[];
export {};
