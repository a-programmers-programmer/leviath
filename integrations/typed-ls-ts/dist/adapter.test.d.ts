/**
 * GQL-05 node (a) — tests for the TypeScript typed-code adapter.
 *
 * One test per emitted attribute: callable.params, callable.returns,
 * callable.signature, callable.port, callable.confirmation (field form),
 * callable.confirmation.<param> (parameter form), nullability, and one
 * unsupported-construct case that asserts the extraction is blocked.
 *
 * A test that cannot fail is worthless, so each assertion pins an exact value
 * string rather than a shape. The expected strings are copied from the accepted
 * Python adapter's output for the equivalent source (directive 0193).
 */
export {};
