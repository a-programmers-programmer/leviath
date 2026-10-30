"""Handwritten resolvers. The generator seeds this file and never rewrites it.

Put behaviour here.  The generated package (``leviath_generated``) supplies the
typed shapes, the operation documents and the client; this file supplies the code
that answers them.  Nothing in this file is regenerated, so edits survive.
"""

from __future__ import annotations

from typing import Callable, Mapping

#: Root fields the accepted contract declares, grouped by operation kind.
QUERY_FIELDS: tuple[str, ...] = (
    "searchWorkflows",
    "workflows",
    "workflow",
    "workflowByName",
    "workflowRun",
    "edgeInvariantRules",
)
MUTATION_FIELDS: tuple[str, ...] = (
    "dispatchWorkflow",
    "runWorkflowStep",
    "respondToGate",
)


def resolve_workflow(context: Mapping[str, object], info: object, args: Mapping[str, object]) -> Mapping[str, object]:
    """Return the workflow named by ``args["id"]``.

    Replace this body with the real read.  The signature is the only contract the
    generated client needs: a root field name maps to one resolver.
    """
    raise NotImplementedError("handwritten: implement resolve_workflow")


def dispatch_workflow(context: Mapping[str, object], info: object, args: Mapping[str, object]) -> Mapping[str, object]:
    """Dispatch a whole workflow.  Behaviour lives here, never in generated files."""
    raise NotImplementedError("handwritten: implement dispatch_workflow")


def transport(document: str, variables: Mapping[str, object]) -> Mapping[str, object]:
    """The operation transport the generated client calls.

    One function, so the generated client stays free of transport choices.
    """
    raise NotImplementedError("handwritten: implement transport")


#: Root field name -> resolver. Add entries as you implement them.
RESOLVERS: Mapping[str, Callable[..., Mapping[str, object]]] = {
    "workflow": resolve_workflow,
    "dispatchWorkflow": dispatch_workflow,
}
