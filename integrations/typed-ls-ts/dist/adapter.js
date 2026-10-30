/**
 * TypeScript typed-code adapter: declared callable ports and metadata, read
 * statically.
 *
 * This is the TypeScript counterpart of
 * `leviath_typed_ls.typed_code_adapter`. It emits the **same fact shape** and
 * the **same value spellings** for the same declaration, so a fact emitted here
 * is directly comparable with a fact emitted by the Python adapter:
 *
 *   callable.params                     comma-joined `name:Type` pieces
 *   callable.returns                    canonical return type
 *   callable.signature                  `(params)->returns`
 *   callable.port                       the declared port interface name
 *   callable.confirmation.<paramName>   `by=<authority>` for a wrapped parameter
 *   callable.confirmation               `by=<authority>` for a wrapped class field
 *
 * Reading is entirely static. `typescript@7.0.2` here is the native port: it
 * exposes a scanner but no in-process text->AST parser, and the high-level API
 * spawns a second server process, which this node must not do. So the token
 * stream is read with `createScanner` and walked by a small recursive-descent
 * reader. The analysed file is never imported, evaluated or executed.
 */
import { canonicalType, confirmationOf, paramShape, PORT_INTERFACES, } from "./canonical.js";
import { fact } from "./facts.js";
import { isMemberModifier, isName, lineOf, lineStarts, readTokens, TOKENS, } from "./reader.js";
import { COMPUTED_NAME_CONSTRUCT, COMPUTED_NAME_OBLIGATION, COMPUTED_NAME_REASON, findStructuralFindings, findTextMarkers, } from "./unsupported.js";
/**
 * A cursor over the token stream. Every reader consumes forward; nothing here
 * inspects the analysed file, so a read can never execute it.
 */
class Cursor {
    tokens;
    source;
    index = 0;
    constructor(tokens, source) {
        this.tokens = tokens;
        this.source = source;
    }
    peek(offset = 0) {
        return this.tokens[this.index + offset];
    }
    next() {
        const token = this.tokens[this.index];
        if (token !== undefined) {
            this.index += 1;
        }
        return token;
    }
    atEnd() {
        return this.index >= this.tokens.length;
    }
    eat(kind) {
        if (this.peek()?.kind === kind) {
            this.index += 1;
            return true;
        }
        return false;
    }
    /** The raw source text spanned by `[first, last)` tokens, unconsumed. */
    slice(from, to) {
        return this.source.slice(from, to);
    }
}
/** Token kinds that close a type at bracket depth zero. */
function isTypeTerminator(kind) {
    return (kind === TOKENS.comma ||
        kind === TOKENS.closeParen ||
        kind === TOKENS.semicolon ||
        kind === TOKENS.equals ||
        kind === TOKENS.closeBrace);
}
/**
 * Read one written type, starting at the token after the `:`.
 *
 * The type is returned as the *verbatim source slice* it spans, so a spelling
 * the adapter does not model (a generic wrapper, an indexed access) survives
 * exactly as written and surfaces as a divergence instead of being smoothed
 * away. Reading a slice is still static: it is text, never evaluation.
 */
function readTypeText(cursor) {
    const first = cursor.peek();
    if (first === undefined) {
        return "";
    }
    const startOffset = first.start;
    let endOffset = startOffset;
    let depth = 0;
    for (;;) {
        const token = cursor.peek();
        if (token === undefined) {
            break;
        }
        if (depth === 0) {
            // A `{` after some type text starts a body, not a type member.
            if (token.kind === TOKENS.openBrace && token.start > startOffset) {
                break;
            }
            // An arrow after some type text is a function-expression body.
            if (token.kind === TOKENS.arrow && token.start > startOffset) {
                break;
            }
            if (isTypeTerminator(token.kind)) {
                break;
            }
        }
        if (token.kind === TOKENS.openBracket ||
            token.kind === TOKENS.lessThan ||
            token.kind === TOKENS.openParen ||
            token.kind === TOKENS.openBrace) {
            depth += 1;
        }
        else if (token.kind === TOKENS.closeBracket ||
            token.kind === TOKENS.greaterThan ||
            token.kind === TOKENS.closeBrace) {
            depth -= 1;
        }
        cursor.next();
        endOffset = token.end;
    }
    return cursor.slice(startOffset, endOffset).trim();
}
/** Read a parameter list, starting at the `(`. */
function readParameters(cursor) {
    const params = [];
    const confirmations = [];
    if (!cursor.eat(TOKENS.openParen)) {
        return { params, confirmations };
    }
    while (!cursor.atEnd() && cursor.peek().kind !== TOKENS.closeParen) {
        // Skip decorators, modifiers and a rest marker on the parameter.
        while (cursor.peek() !== undefined &&
            (cursor.peek().kind === TOKENS.at ||
                cursor.peek().kind === TOKENS.dotDotDot ||
                isMemberModifier(cursor.peek()))) {
            cursor.next();
        }
        const nameToken = cursor.peek();
        if (nameToken === undefined || !isName(nameToken)) {
            // Something this reader does not model: step past it, never loop.
            if (cursor.next() === undefined) {
                break;
            }
            continue;
        }
        cursor.next();
        const name = nameToken.value.length > 0 ? nameToken.value : nameToken.text;
        const optional = cursor.eat(TOKENS.question);
        let written;
        if (cursor.eat(TOKENS.colon)) {
            written = readTypeText(cursor);
        }
        const authority = confirmationOf(written);
        if (authority !== undefined) {
            confirmations.push({
                name,
                authority,
                line: nameToken.line,
                col: nameToken.colStart,
            });
        }
        params.push(paramShape(name, written, optional));
        cursor.eat(TOKENS.comma);
    }
    cursor.eat(TOKENS.closeParen);
    return { params, confirmations };
}
/** Read a callable's signature; its name token is already consumed. */
function readCallable(cursor, nameToken) {
    const { params, confirmations } = readParameters(cursor);
    let written;
    if (cursor.eat(TOKENS.colon)) {
        written = readTypeText(cursor);
    }
    return {
        name: nameToken.value.length > 0 ? nameToken.value : nameToken.text,
        params,
        returns: written === undefined || written === "" ? "any" : canonicalType(written),
        confirmations,
        line: nameToken.line,
        col: nameToken.colStart,
    };
}
/** Skip a balanced `{ ... }` / `( ... )` / `[ ... ]` group, opener consumed. */
function skipBalanced(cursor, open, close) {
    let depth = 1;
    while (!cursor.atEnd() && depth > 0) {
        const token = cursor.next();
        if (token.kind === open) {
            depth += 1;
        }
        else if (token.kind === close) {
            depth -= 1;
        }
    }
}
/** Read a class body, starting after the class `{`. */
function readClassBody(cursor) {
    const callables = [];
    const fields = [];
    const computedNames = [];
    let depth = 1;
    while (!cursor.atEnd() && depth > 0) {
        const token = cursor.peek();
        if (token.kind === TOKENS.openBrace) {
            depth += 1;
            cursor.next();
            continue;
        }
        if (token.kind === TOKENS.closeBrace) {
            depth -= 1;
            cursor.next();
            continue;
        }
        if (depth > 1) {
            cursor.next();
            continue;
        }
        // A `[` where a member name is expected is a computed member name: its name
        // is computed at run time and cannot be read statically.
        if (token.kind === TOKENS.openBracket) {
            computedNames.push(token);
            cursor.next();
            skipBalanced(cursor, TOKENS.openBracket, TOKENS.closeBracket);
            continue;
        }
        // Skip decorators and member modifiers.
        if (token.kind === TOKENS.at || isMemberModifier(token)) {
            cursor.next();
            continue;
        }
        if (!isName(token)) {
            cursor.next();
            continue;
        }
        cursor.next();
        // A method or an accessor: a callable.
        if (cursor.peek()?.kind === TOKENS.openParen) {
            callables.push(readCallable(cursor, token));
            continue;
        }
        // A field: `name?: Type`, possibly followed by `= initialiser`.
        const name = token.value.length > 0 ? token.value : token.text;
        cursor.eat(TOKENS.question);
        if (cursor.eat(TOKENS.colon)) {
            const written = readTypeText(cursor);
            const authority = confirmationOf(written);
            if (authority !== undefined) {
                fields.push({
                    name,
                    authority,
                    line: token.line,
                    col: token.colStart,
                });
            }
        }
        if (cursor.eat(TOKENS.equals)) {
            let nested = 0;
            while (!cursor.atEnd()) {
                const nextToken = cursor.peek();
                if (nextToken.kind === TOKENS.openBrace ||
                    nextToken.kind === TOKENS.openParen ||
                    nextToken.kind === TOKENS.openBracket) {
                    nested += 1;
                }
                else if (nextToken.kind === TOKENS.closeBrace ||
                    nextToken.kind === TOKENS.closeParen ||
                    nextToken.kind === TOKENS.closeBracket) {
                    if (nested === 0) {
                        break;
                    }
                    nested -= 1;
                }
                else if (nested === 0 && nextToken.kind === TOKENS.semicolon) {
                    cursor.next();
                    break;
                }
                cursor.next();
            }
        }
        cursor.eat(TOKENS.semicolon);
    }
    return { callables, fields, computedNames };
}
/** Read a class declaration; `class` and its name token are already consumed. */
function readClass(cursor, nameToken) {
    const ports = [];
    // Heritage: `extends Base<T>` / `implements PortA, PortB`.
    for (;;) {
        const token = cursor.peek();
        if (token === undefined || token.kind === TOKENS.openBrace) {
            break;
        }
        if (token.kind === TOKENS.implementsKeyword || token.kind === TOKENS.extendsKeyword) {
            cursor.next();
            continue;
        }
        if (isName(token)) {
            const heritageName = token.value.length > 0 ? token.value : token.text;
            if (PORT_INTERFACES.includes(heritageName)) {
                ports.push(heritageName);
            }
            cursor.next();
            // A generic base list (`Base<T>`) is not a port declaration.
            if (cursor.peek()?.kind === TOKENS.lessThan) {
                cursor.next();
                skipBalanced(cursor, TOKENS.lessThan, TOKENS.greaterThan);
            }
            continue;
        }
        cursor.next();
    }
    cursor.eat(TOKENS.openBrace);
    const { callables, fields, computedNames } = readClassBody(cursor);
    return {
        name: nameToken.value.length > 0 ? nameToken.value : nameToken.text,
        ports,
        callables,
        fields,
        computedNames,
        line: nameToken.line,
        col: nameToken.colStart,
    };
}
/** Emit the signature facts for one callable under `symbolKey`. */
function callableFacts(symbolKey, callable, path) {
    const params = callable.params.join(",");
    const facts = [
        fact(symbolKey, "callable.params", params, path, callable.line, callable.col),
        fact(symbolKey, "callable.returns", callable.returns, path, callable.line, callable.col),
        fact(symbolKey, "callable.signature", `(${params})->${callable.returns}`, path, callable.line, callable.col),
    ];
    for (const confirmation of callable.confirmations) {
        facts.push(fact(symbolKey, `callable.confirmation.${confirmation.name}`, `by=${confirmation.authority}`, path, confirmation.line, confirmation.col));
    }
    return facts;
}
/**
 * Extract one typed-TypeScript artifact statically.
 *
 * Never imports, never executes, never evaluates the artifact. A construct that
 * cannot be read statically becomes an `UnsupportedConstruct`, and the
 * extraction is then `blocked`.
 */
export function extractTyped(source, path = "module.ts") {
    const { tokens, triviaSpans } = readTokens(source);
    const starts = lineStarts(source);
    const at = (offset) => lineOf(starts, offset);
    const unsupported = [];
    // Text markers (eval, exec, setattr, ...), skipping comments and literals.
    unsupported.push(...findTextMarkers(source, path, at, triviaSpans));
    // Structural markers: wildcard import/export, read from the token stream.
    unsupported.push(...findStructuralFindings(tokens, path, {
        import: TOKENS.importKeyword,
        export: TOKENS.exportKeyword,
        star: TOKENS.asterisk,
    }));
    const facts = [];
    const classes = [];
    const functions = [];
    const computedNames = [];
    const cursor = new Cursor(tokens, source);
    // The start of the current declaration statement, including its leading
    // modifiers, so the source span points at the declaration and not just its
    // name. This mirrors the Python adapter, whose span starts at the statement.
    let declarationStart;
    while (!cursor.atEnd()) {
        const token = cursor.peek();
        if (token.kind === TOKENS.exportKeyword ||
            token.kind === TOKENS.declareKeyword ||
            token.kind === TOKENS.defaultKeyword) {
            declarationStart ??= token;
            cursor.next();
            continue;
        }
        if (token.kind === TOKENS.classKeyword) {
            cursor.next();
            const nameToken = cursor.peek();
            if (nameToken === undefined || !isName(nameToken)) {
                declarationStart = undefined;
                continue;
            }
            cursor.next();
            const declared = readClass(cursor, nameToken);
            const start = declarationStart ?? token;
            declared.line = start.line;
            declared.col = start.colStart;
            classes.push(declared);
            declarationStart = undefined;
            continue;
        }
        if (token.kind === TOKENS.functionKeyword) {
            const functionStart = declarationStart ?? token;
            cursor.next();
            if (cursor.peek()?.kind === TOKENS.asterisk) {
                cursor.next();
            }
            const nameToken = cursor.peek();
            if (nameToken === undefined || !isName(nameToken)) {
                declarationStart = undefined;
                continue;
            }
            cursor.next();
            const callable = readCallable(cursor, nameToken);
            callable.line = functionStart.line;
            callable.col = functionStart.colStart;
            functions.push({ callable });
            // Skip the body, so its statements are never read as declarations.
            if (cursor.peek()?.kind === TOKENS.openBrace) {
                cursor.next();
                skipBalanced(cursor, TOKENS.openBrace, TOKENS.closeBrace);
            }
            declarationStart = undefined;
            continue;
        }
        if (token.kind === TOKENS.interfaceKeyword) {
            // An interface declares no signature fact here; it is read for its name
            // only, which a class declares in its heritage.
            cursor.next();
            while (!cursor.atEnd() && cursor.peek().kind !== TOKENS.openBrace) {
                cursor.next();
            }
            if (cursor.eat(TOKENS.openBrace)) {
                skipBalanced(cursor, TOKENS.openBrace, TOKENS.closeBrace);
            }
            declarationStart = undefined;
            continue;
        }
        declarationStart = undefined;
        cursor.next();
    }
    for (const cls of classes) {
        for (const port of cls.ports) {
            facts.push(fact(cls.name, "callable.port", port, path, cls.line, cls.col));
        }
        for (const callable of cls.callables) {
            facts.push(...callableFacts(`${cls.name}.${callable.name}`, callable, path));
        }
        for (const field of cls.fields) {
            facts.push(fact(`${cls.name}.${field.name}`, "callable.confirmation", `by=${field.authority}`, path, field.line, field.col));
        }
        computedNames.push(...cls.computedNames);
    }
    for (const declared of functions) {
        facts.push(...callableFacts(declared.callable.name, declared.callable, path));
    }
    for (const computed of computedNames) {
        unsupported.push({
            construct: COMPUTED_NAME_CONSTRUCT,
            path,
            line: computed.line,
            reason: COMPUTED_NAME_REASON,
            obligation: COMPUTED_NAME_OBLIGATION,
        });
    }
    return {
        projection: { name: "code", facts },
        unsupported,
        warnings: [],
        mode: "typescript-scanner-static",
        blocked: unsupported.length > 0,
    };
}
//# sourceMappingURL=adapter.js.map