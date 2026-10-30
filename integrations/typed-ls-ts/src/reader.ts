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

import { LanguageVariant, SyntaxKind } from "typescript/unstable/ast";
import { createScanner } from "typescript/unstable/ast/scanner";

/** The token kinds this reader compares against. */
export const TOKENS = {
  endOfFile: SyntaxKind.EndOfFile,
  identifier: SyntaxKind.Identifier,
  stringLiteral: SyntaxKind.StringLiteral,
  numericLiteral: SyntaxKind.NumericLiteral,
  noSubstitutionTemplate: SyntaxKind.NoSubstitutionTemplateLiteral,
  templateHead: SyntaxKind.TemplateHead,
  templateMiddle: SyntaxKind.TemplateMiddle,
  templateTail: SyntaxKind.TemplateTail,
  regularExpression: SyntaxKind.RegularExpressionLiteral,
  openBrace: SyntaxKind.OpenBraceToken,
  closeBrace: SyntaxKind.CloseBraceToken,
  openParen: SyntaxKind.OpenParenToken,
  closeParen: SyntaxKind.CloseParenToken,
  openBracket: SyntaxKind.OpenBracketToken,
  closeBracket: SyntaxKind.CloseBracketToken,
  lessThan: SyntaxKind.LessThanToken,
  greaterThan: SyntaxKind.GreaterThanToken,
  colon: SyntaxKind.ColonToken,
  comma: SyntaxKind.CommaToken,
  semicolon: SyntaxKind.SemicolonToken,
  question: SyntaxKind.QuestionToken,
  bar: SyntaxKind.BarToken,
  ampersand: SyntaxKind.AmpersandToken,
  asterisk: SyntaxKind.AsteriskToken,
  equals: SyntaxKind.EqualsToken,
  at: SyntaxKind.AtToken,
  exclamation: SyntaxKind.ExclamationToken,
  dot: SyntaxKind.DotToken,
  dotDotDot: SyntaxKind.DotDotDotToken,
  slash: SyntaxKind.SlashToken,
  slashEquals: SyntaxKind.SlashEqualsToken,
  arrow: SyntaxKind.EqualsGreaterThanToken,
  classKeyword: SyntaxKind.ClassKeyword,
  interfaceKeyword: SyntaxKind.InterfaceKeyword,
  functionKeyword: SyntaxKind.FunctionKeyword,
  exportKeyword: SyntaxKind.ExportKeyword,
  importKeyword: SyntaxKind.ImportKeyword,
  implementsKeyword: SyntaxKind.ImplementsKeyword,
  extendsKeyword: SyntaxKind.ExtendsKeyword,
  typeKeyword: SyntaxKind.TypeKeyword,
  fromKeyword: SyntaxKind.FromKeyword,
  newKeyword: SyntaxKind.NewKeyword,
  constructorKeyword: SyntaxKind.ConstructorKeyword,
  publicKeyword: SyntaxKind.PublicKeyword,
  privateKeyword: SyntaxKind.PrivateKeyword,
  protectedKeyword: SyntaxKind.ProtectedKeyword,
  overrideKeyword: SyntaxKind.OverrideKeyword,
  readonlyKeyword: SyntaxKind.ReadonlyKeyword,
  staticKeyword: SyntaxKind.StaticKeyword,
  abstractKeyword: SyntaxKind.AbstractKeyword,
  declareKeyword: SyntaxKind.DeclareKeyword,
  asyncKeyword: SyntaxKind.AsyncKeyword,
  getKeyword: SyntaxKind.GetKeyword,
  setKeyword: SyntaxKind.SetKeyword,
  defaultKeyword: SyntaxKind.DefaultKeyword,
  voidKeyword: SyntaxKind.VoidKeyword,
  nullKeyword: SyntaxKind.NullKeyword,
  undefinedKeyword: SyntaxKind.UndefinedKeyword,
} as const;

/** Modifier keywords a class member may carry before its name. */
const MEMBER_MODIFIERS: ReadonlySet<number> = new Set([
  TOKENS.publicKeyword,
  TOKENS.privateKeyword,
  TOKENS.protectedKeyword,
  TOKENS.readonlyKeyword,
  TOKENS.staticKeyword,
  TOKENS.abstractKeyword,
  TOKENS.overrideKeyword,
  TOKENS.declareKeyword,
  TOKENS.asyncKeyword,
]);

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

/** Text spans the scanner reports as trivia, used to skip false markers. */
const TRIVIA_MIN = SyntaxKind.FirstTriviaToken;
const TRIVIA_MAX = SyntaxKind.LastTriviaToken;

/** True when a `/` at this point can start a regular-expression literal. */
function allowsRegExp(previous: Token | undefined): boolean {
  if (previous === undefined) {
    return true;
  }
  switch (previous.kind) {
    case TOKENS.identifier:
    case TOKENS.stringLiteral:
    case TOKENS.numericLiteral:
    case TOKENS.noSubstitutionTemplate:
    case TOKENS.templateTail:
    case TOKENS.regularExpression:
    case TOKENS.closeParen:
    case TOKENS.closeBracket:
    case TOKENS.closeBrace:
    case TOKENS.exclamation:
      return false;
    default:
      return true;
  }
}

/** Precompute the offset of the start of every line. */
export function lineStarts(text: string): number[] {
  const starts = [0];
  for (let i = 0; i < text.length; i += 1) {
    const character = text[i]!;
    if (character === "\n") {
      starts.push(i + 1);
    } else if (character === "\r") {
      starts.push(i + (text[i + 1] === "\n" ? 2 : 1));
      if (text[i + 1] === "\n") {
        i += 1;
      }
    } else if (character === "\u2028" || character === "\u2029") {
      starts.push(i + 1);
    }
  }
  return starts;
}

/** Binary search the line number for an offset. */
export function lineOf(starts: readonly number[], offset: number): number {
  let low = 0;
  let high = starts.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high + 1) / 2);
    if (starts[middle]! <= offset) {
      low = middle;
    } else {
      high = middle - 1;
    }
  }
  return low + 1;
}

/**
 * Read every significant token of `text`.
 *
 * Trivia (comments and whitespace) is skipped, but the spans it covered are
 * returned too, so a text marker inside a comment is not mistaken for code.
 */
export function readTokens(text: string): {
  tokens: Token[];
  triviaSpans: Array<[number, number]>; // [start, end)
} {
  const scanner = createScanner(true, LanguageVariant.Standard, text);
  const starts = lineStarts(text);
  const tokens: Token[] = [];
  const triviaSpans: Array<[number, number]> = [];
  let previous: Token | undefined;

  let kind = scanner.scan();
  while (kind !== TOKENS.endOfFile) {
    const start = scanner.getTokenStart();
    if (kind === TOKENS.slash && allowsRegExp(previous)) {
      const rescanned = scanner.reScanSlashToken();
      if (rescanned === TOKENS.regularExpression) {
        kind = rescanned;
      }
    }
    const end = scanner.getTokenEnd();
    const line = lineOf(starts, start);
    const token: Token = {
      kind,
      text: scanner.getTokenText(),
      value: scanner.getTokenValue(),
      start,
      end,
      line,
      colStart: start - starts[line - 1]!,
      lineBreakBefore: scanner.hasPrecedingLineBreak(),
    };
    tokens.push(token);
    previous = token;
    kind = scanner.scan();
  }

  // Collect comment and literal spans separately, so a marker inside a comment
  // or a string is skipped: those are data, not a dynamic construct.
  const triviaScanner = createScanner(false, LanguageVariant.Standard, text);
  let triviaKind = triviaScanner.scan();
  while (triviaKind !== TOKENS.endOfFile) {
    const isCommentOrTrivia = triviaKind >= TRIVIA_MIN && triviaKind <= TRIVIA_MAX;
    const isLiteral =
      triviaKind === TOKENS.stringLiteral ||
      triviaKind === TOKENS.noSubstitutionTemplate ||
      triviaKind === TOKENS.templateHead ||
      triviaKind === TOKENS.templateMiddle ||
      triviaKind === TOKENS.templateTail;
    if (isCommentOrTrivia || isLiteral) {
      triviaSpans.push([triviaScanner.getTokenStart(), triviaScanner.getTokenEnd()]);
    }
    triviaKind = triviaScanner.scan();
  }

  return { tokens, triviaSpans };
}

/** True when `token` is one of the member-modifier keywords. */
export function isMemberModifier(token: Token): boolean {
  return MEMBER_MODIFIERS.has(token.kind);
}

/** True when `token` names something (an identifier, or a keyword used as one). */
export function isName(token: Token | undefined): boolean {
  if (token === undefined) {
    return false;
  }
  return token.kind === TOKENS.identifier || (token.kind >= SyntaxKind.FirstKeyword && token.kind <= SyntaxKind.LastKeyword);
}