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
export {};
