/**
 * Handwritten resolvers. The generator seeds this file and never rewrites it.
 *
 * Put behaviour here.  The generated package supplies the typed shapes, the
 * operation documents and the client; this file supplies the code that answers
 * them.  Nothing in this file is regenerated, so edits survive.
 */

export const ROOT_FIELDS: ReadonlyArray<string> = [
  "searchWorkflows",
  "workflows",
  "workflow",
  "workflowByName",
  "workflowRun",
  "edgeInvariantRules",
  "dispatchWorkflow",
  "runWorkflowStep",
  "respondToGate",
];

export function resolveWorkflow(
  _context: Readonly<Record<string, unknown>>,
  _args: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  throw new Error("handwritten: implement resolveWorkflow");
}

export function dispatchWorkflow(
  _context: Readonly<Record<string, unknown>>,
  _args: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  throw new Error("handwritten: implement dispatchWorkflow");
}

/** The operation transport the generated client calls. */
export function transport(
  _document: string,
  _variables: Readonly<Record<string, unknown>>,
): Readonly<Record<string, unknown>> {
  throw new Error("handwritten: implement transport");
}

export const resolvers = {
  workflow: resolveWorkflow,
  dispatchWorkflow,
};
