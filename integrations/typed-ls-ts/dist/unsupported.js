/**
 * Explicit, locatable findings for constructs the static reader will not guess
 * about.
 *
 * This mirrors `UnsupportedConstruct` in the Python adapter
 * (`leviath_typed_ls.typed_code_adapter`): the same five fields, the same
 * "obligation" wording, and the same rule that any finding marks the extraction
 * `blocked`. A construct is never silently skipped and never guessed.
 */
import { TOKENS } from "./reader.js";
/**
 * Language features that make a declaration dynamic. Each name is the Python
 * adapter's counterpart where one exists, so the two adapters report the same
 * problem in the same terms.
 */
export const DYNAMIC_MARKERS = [
    {
        marker: "eval(",
        reason: "source is built at run time",
        obligation: "write the declaration literally",
    },
    {
        marker: "new Function(",
        reason: "source is built at run time",
        obligation: "write the declaration literally",
    },
    {
        marker: "Object.defineProperty(",
        reason: "attributes are created at run time",
        obligation: "declare the attribute in the class body",
    },
    {
        marker: "Object.defineProperties(",
        reason: "attributes are created at run time",
        obligation: "declare the attribute in the class body",
    },
    {
        marker: "Object.assign(",
        reason: "attributes are created at run time",
        obligation: "declare the attribute in the class body",
    },
    {
        marker: "Reflect.set(",
        reason: "attributes are created at run time",
        obligation: "declare the attribute in the class body",
    },
    {
        marker: "Reflect.defineProperty(",
        reason: "attributes are created at run time",
        obligation: "declare the attribute in the class body",
    },
    {
        marker: "globalThis[",
        reason: "module namespace is mutated at run time",
        obligation: "declare a module-level symbol",
    },
    {
        marker: "__proto__",
        reason: "prototype members are installed at run time",
        obligation: "declare the member in the class body",
    },
];
/** Computed member name: `[nameFor()]: string`. */
export const COMPUTED_NAME_CONSTRUCT = "computed member name";
export const COMPUTED_NAME_REASON = "the member name is computed at run time";
export const COMPUTED_NAME_OBLIGATION = "declare the member with a literal name";
/** Wildcard import: `import * as ns from "..."`. */
export const WILDCARD_IMPORT_CONSTRUCT = "import *";
export const WILDCARD_IMPORT_REASON = "wildcard import hides which names are declared";
export const WILDCARD_IMPORT_OBLIGATION = "import the names explicitly";
/** Wildcard re-export: `export * from "..."`. */
export const WILDCARD_EXPORT_CONSTRUCT = "export *";
export const WILDCARD_EXPORT_REASON = "wildcard re-export hides which names are declared";
export const WILDCARD_EXPORT_OBLIGATION = "export the names explicitly";
/** True when `offset` falls inside one of the skipped spans. */
function inSpans(offset, spans) {
    for (const [start, end] of spans) {
        if (offset >= start && offset < end) {
            return true;
        }
    }
    return false;
}
/**
 * Find every dynamic text marker, skipping comments and string literals.
 *
 * Skipping those spans matters: a marker mentioned in a comment or inside a
 * string is data, not a construct, and blocking on it would be a false alarm.
 */
export function findTextMarkers(source, path, lineAt, skipSpans) {
    const findings = [];
    for (const { marker, reason, obligation } of DYNAMIC_MARKERS) {
        let from = 0;
        for (;;) {
            const found = source.indexOf(marker, from);
            if (found === -1) {
                break;
            }
            from = found + marker.length;
            if (inSpans(found, skipSpans)) {
                continue;
            }
            findings.push({
                construct: marker,
                path,
                line: lineAt(found),
                reason,
                obligation,
            });
        }
    }
    return findings;
}
/**
 * Find structural markers that cannot be found by text alone.
 *
 * `import *` is reported whether or not it binds a name, and `export *` is
 * reported separately, because they hide which names are declared in two
 * different directions.
 */
export function findStructuralFindings(tokens, path, kinds) {
    const findings = [];
    for (let index = 0; index < tokens.length; index += 1) {
        const token = tokens[index];
        if (token.kind !== kinds.import && token.kind !== kinds.export) {
            continue;
        }
        // Only an `import`/`export` *statement* counts; a property named `import`
        // does not.
        if (index > 0) {
            const previous = tokens[index - 1];
            if (previous.kind === TOKENS.dot ||
                previous.kind === TOKENS.openBrace ||
                previous.kind === TOKENS.colon) {
                continue;
            }
        }
        if (tokens[index + 1]?.kind !== kinds.star) {
            continue;
        }
        if (token.kind === kinds.import) {
            findings.push({
                construct: WILDCARD_IMPORT_CONSTRUCT,
                path,
                line: token.line,
                reason: WILDCARD_IMPORT_REASON,
                obligation: WILDCARD_IMPORT_OBLIGATION,
            });
        }
        else {
            findings.push({
                construct: WILDCARD_EXPORT_CONSTRUCT,
                path,
                line: token.line,
                reason: WILDCARD_EXPORT_REASON,
                obligation: WILDCARD_EXPORT_OBLIGATION,
            });
        }
    }
    return findings;
}
//# sourceMappingURL=unsupported.js.map