/**
 * The token reader: a typed TypeScript source file, read statically.
 *
 * `typescript@7.0.2` on this box is the native port. It exposes a scanner
 * (`typescript/unstable/ast/scanner`) and enum tables
 * (`typescript/unstable/ast`), but no in-process text->AST parser:
 * `createSourceFile` takes already-built statements, and the high-level API
 * spawns a second server process, which this node is forbidden to do.
 *
 * So the adapter reads the token stream with `createScanner` and walks it with
 * a small hand-written recursive-descent reader. Nothing here imports, evals or
 * executes the analysed file: the scanner is a pure function of the text.
 */
import { SyntaxKind } from "typescript/unstable/ast";
/** The token kinds this reader compares against. */
export declare const TOKENS: {
    readonly endOfFile: SyntaxKind.EndOfFile;
    readonly identifier: SyntaxKind.Identifier;
    readonly stringLiteral: SyntaxKind.StringLiteral;
    readonly numericLiteral: SyntaxKind.NumericLiteral;
    readonly noSubstitutionTemplate: SyntaxKind.NoSubstitutionTemplateLiteral;
    readonly templateHead: SyntaxKind.TemplateHead;
    readonly templateMiddle: SyntaxKind.TemplateMiddle;
    readonly templateTail: SyntaxKind.TemplateTail;
    readonly regularExpression: SyntaxKind.RegularExpressionLiteral;
    readonly openBrace: SyntaxKind.OpenBraceToken;
    readonly closeBrace: SyntaxKind.CloseBraceToken;
    readonly openParen: SyntaxKind.OpenParenToken;
    readonly closeParen: SyntaxKind.CloseParenToken;
    readonly openBracket: SyntaxKind.OpenBracketToken;
    readonly closeBracket: SyntaxKind.CloseBracketToken;
    readonly lessThan: SyntaxKind.LessThanToken;
    readonly greaterThan: SyntaxKind.GreaterThanToken;
    readonly colon: SyntaxKind.ColonToken;
    readonly comma: SyntaxKind.CommaToken;
    readonly semicolon: SyntaxKind.SemicolonToken;
    readonly question: SyntaxKind.QuestionToken;
    readonly bar: SyntaxKind.BarToken;
    readonly ampersand: SyntaxKind.AmpersandToken;
    readonly asterisk: SyntaxKind.AsteriskToken;
    readonly equals: SyntaxKind.EqualsToken;
    readonly at: SyntaxKind.AtToken;
    readonly exclamation: SyntaxKind.ExclamationToken;
    readonly dot: SyntaxKind.DotToken;
    readonly dotDotDot: SyntaxKind.DotDotDotToken;
    readonly slash: SyntaxKind.SlashToken;
    readonly slashEquals: SyntaxKind.SlashEqualsToken;
    readonly arrow: SyntaxKind.EqualsGreaterThanToken;
    readonly classKeyword: SyntaxKind.ClassKeyword;
    readonly interfaceKeyword: SyntaxKind.InterfaceKeyword;
    readonly functionKeyword: SyntaxKind.FunctionKeyword;
    readonly exportKeyword: SyntaxKind.ExportKeyword;
    readonly importKeyword: SyntaxKind.ImportKeyword;
    readonly implementsKeyword: SyntaxKind.ImplementsKeyword;
    readonly extendsKeyword: SyntaxKind.ExtendsKeyword;
    readonly typeKeyword: SyntaxKind.TypeKeyword;
    readonly fromKeyword: SyntaxKind.FromKeyword;
    readonly newKeyword: SyntaxKind.NewKeyword;
    readonly constructorKeyword: SyntaxKind.ConstructorKeyword;
    readonly publicKeyword: SyntaxKind.PublicKeyword;
    readonly privateKeyword: SyntaxKind.PrivateKeyword;
    readonly protectedKeyword: SyntaxKind.ProtectedKeyword;
    readonly overrideKeyword: SyntaxKind.OverrideKeyword;
    readonly readonlyKeyword: SyntaxKind.ReadonlyKeyword;
    readonly staticKeyword: SyntaxKind.StaticKeyword;
    readonly abstractKeyword: SyntaxKind.AbstractKeyword;
    readonly declareKeyword: SyntaxKind.DeclareKeyword;
    readonly asyncKeyword: SyntaxKind.AsyncKeyword;
    readonly getKeyword: SyntaxKind.GetKeyword;
    readonly setKeyword: SyntaxKind.SetKeyword;
    readonly defaultKeyword: SyntaxKind.DefaultKeyword;
    readonly voidKeyword: SyntaxKind.VoidKeyword;
    readonly nullKeyword: SyntaxKind.NullKeyword;
    readonly undefinedKeyword: SyntaxKind.UndefinedKeyword;
};
/** A token's start position and its end, as offsets into the source text. */
export interface Token {
    kind: number;
    /** The raw text of the token, exactly as written. */
    text: string;
    /** The cooked value for identifiers, strings and numbers. */
    value: string;
    /** Start offset into the source text. */
    start: number;
    /** End offset into the source text (exclusive). */
    end: number;
    /** 1-based line number of the token start. */
    line: number;
    /** 0-based column of the token start. */
    colStart: number;
    /** True when a line break precedes this token. */
    lineBreakBefore: boolean;
}
/** Precompute the offset of the start of every line. */
export declare function lineStarts(text: string): number[];
/** Binary search the line number for an offset. */
export declare function lineOf(starts: readonly number[], offset: number): number;
/**
 * Read every significant token of `text`.
 *
 * Trivia (comments and whitespace) is skipped, but the spans it covered are
 * returned too, so a text marker inside a comment is not mistaken for code.
 */
export declare function readTokens(text: string): {
    tokens: Token[];
    triviaSpans: Array<[number, number]>;
};
/** True when `token` is one of the member-modifier keywords. */
export declare function isMemberModifier(token: Token): boolean;
/** True when `token` names something (an identifier, or a keyword used as one). */
export declare function isName(token: Token | undefined): boolean;
