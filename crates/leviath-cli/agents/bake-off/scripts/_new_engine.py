# ── merge engine ─────────────────────────────────────────────────────────

@dataclass
class MergeDecision:
    """A structured decision for ONE divergent type.

    `sources` maps every decided key of the merged type - field names, enum
    values or union members, depending on the type's kind - to the candidate it
    comes from: ``'a'`` or ``'b'``. `implements` does the same for interface
    names. Both maps are total over the permitted key set; totality and every
    other rule live in :mod:`merge_decisions`, which is the only authority on
    what a legal decision is.

    There is deliberately no way to express a decision in prose here. The
    pre-GQL-09 engine guessed a base side and a field set out of the English in
    `reason` ("adopt B's fields", "restore X from A"); that produced merges no
    reviewer could reproduce from the decision record.
    """

    type_name: str
    action: str                                    # keep_a | keep_b | reconcile
    reason: str = ""
    sources: dict = field(default_factory=dict)
    implements: dict = field(default_factory=dict)


#: type kind -> the AST attribute holding the keys a decision chooses between.
_AST_GROUP_ATTR = {
    'type': 'fields',
    'interface': 'fields',
    'input': 'fields',
    'enum': 'values',
    'union': 'types',
}


def group_keys(td: TypeDef) -> list[str]:
    """The decided key names of a *regex-parsed* TypeDef (diff-side view)."""
    if td.kind in ('type', 'interface', 'input'):
        return list(td.fields.keys())
    if td.kind == 'enum':
        return list(td.enum_values)
    if td.kind == 'union':
        return list(td.union_members)
    return []


def _norm_body(text: str) -> str:
    """Whitespace/comment-insensitive body text, for structural rename proposals."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        lines.append(stripped)
    return ' '.join(lines)


def propose_renames(diff: 'SchemaDiff') -> list[tuple[str, str]]:
    """Propose (only-A name -> only-B name) pairs **structurally**.

    A pair is proposed when the two definitions have the same kind and the same
    normalized body, so the only difference is the name. This replaces the
    hard-coded domain guess list the pre-GQL-09 driver carried
    ("ResourceKind -> ProposalResource" and friends): a rename is now a checked
    structural fact, and a proposed pair still needs an explicit decision.
    """
    proposals = []
    for a_name in diff.only_a:
        a_def = diff.a_types[a_name]
        if a_def.kind in ('scalar', 'schema', 'directive'):
            continue
        a_body = _norm_body(a_def.body.replace(a_name, '@NAME@'))
        for b_name in diff.only_b:
            b_def = diff.b_types[b_name]
            if a_def.kind != b_def.kind:
                continue
            b_body = _norm_body(b_def.body.replace(b_name, '@NAME@'))
            if a_body == b_body:
                proposals.append((a_name, b_name))
    return sorted(proposals)


def _ast_keys(node, attr: str) -> list[str]:
    out = []
    for item in getattr(node, attr, None) or ():
        name = getattr(getattr(item, 'name', None), 'value', None)
        if name and name not in out:
            out.append(name)
    return out


def _ast_key_order(attr: str, a_def, b_def) -> list[str]:
    """A's order first, then B-only keys in B's order - the deterministic merge order."""
    keys = _ast_keys(a_def, attr)
    keys += [k for k in _ast_keys(b_def, attr) if k not in keys]
    return keys


def _reconcile_definition(a_def, b_def, decision: MergeDecision):
    """Build the reconciled AST node for one type from the decision's sources.

    Deterministic by construction: the key order is A-then-B-extra, and each key
    is taken from exactly the side the decision names. Nothing is inferred.
    """
    if b_def is None:
        return a_def
    attr = _AST_GROUP_ATTR.get(a_def.kind)
    base = copy.deepcopy(a_def)
    if attr is None:                     # scalar / schema: nothing keyed to merge
        return base

    a_nodes = {k: n for k, n in zip(_ast_keys(a_def, attr),
                                    getattr(a_def, attr) or ())}
    b_nodes = {k: n for k, n in zip(_ast_keys(b_def, attr),
                                    getattr(b_def, attr) or ())}
    merged = []
    for key in _ast_key_order(attr, a_def, b_def):
        source = decision.sources.get(key)
        if source == 'a' and key in a_nodes:
            merged.append(copy.deepcopy(a_nodes[key]))
        elif source == 'b' and key in b_nodes:
            merged.append(copy.deepcopy(b_nodes[key]))
        else:
            raise ValueError(
                f"reconcile decision for {decision.type_name!r} names source "
                f"{source!r} for {key!r}, which is not available on that side"
            )
    setattr(base, attr, tuple(merged))

    if attr == 'fields' and hasattr(base, 'interfaces'):
        a_ifaces = list(getattr(a_def, 'interfaces', None) or ())
        b_ifaces = list(getattr(b_def, 'interfaces', None) or ())
        by_name = {}
        for node in b_ifaces + a_ifaces:                 # A wins the same name
            by_name[node.name.value] = node
        kept = []
        for name in [n.name.value for n in a_ifaces] + \
                    [n.name.value for n in b_ifaces if n.name.value not in
                     {i.name.value for i in a_ifaces}]:
            source = decision.implements.get(name)
            if source in ('a', 'b'):
                kept.append(copy.deepcopy(by_name[name]))
            else:
                raise ValueError(
                    f"reconcile decision for {decision.type_name!r} has no source "
                    f"for interface {name!r}"
                )
        base.interfaces = tuple(kept)
    return base


def apply_merge(
    seed_a_text: str,
    seed_b_text: str,
    decisions: list,
    renames: Optional[dict] = None,
) -> str:
    """Apply validated decisions to produce a reconciled schema (AST-based).

    The output is always valid SDL because it is produced by
    :func:`graphql.language.print_ast` from a properly-constructed document. No
    string concatenation, no orphaned prose, no duplicate definitions.

    This function trusts its input: the decisions must already have passed
    :func:`merge_decisions.validate`. Callers that hold a decision *document*
    should call :func:`merge_decisions.merge` instead, which validates first.

    Parameters
    ----------
    seed_a_text / seed_b_text: the two candidates.
    decisions: the validated decisions for the divergent types.
    renames:   ``{old_name: new_name}`` for the renames decided as renames.
    """
    doc_a = _gql_parse(seed_a_text)
    doc_b = _gql_parse(seed_b_text)

    # --- 2. Name-indexed maps -----------------------------------------------
    a_by_name: dict = {}
    for d in doc_a.definitions:
        a_by_name[_definition_name(d) or _SCHEMA_SENTINEL] = d
    b_by_name: dict = {}
    for d in doc_b.definitions:
        b_by_name[_definition_name(d) or _SCHEMA_SENTINEL] = d

    all_renames = dict(renames or {})
    decision_map = {d.type_name: d for d in decisions}

    # --- 4. Assemble the merged definition list ----------------------------
    merged_defs: list = []
    emitted: set = set()
    renamed_from_a: set = set(all_renames.keys())

    for d in doc_a.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL

        if name is not None and name in renamed_from_a:
            # Being renamed: B's definition is appended in step 6.
            continue

        if name is not None and name in decision_map:
            dec = decision_map[name]
            if dec.action == 'keep_b' and key in b_by_name:
                d = copy.deepcopy(b_by_name[key])
            elif dec.action == 'reconcile':
                d = _reconcile_definition(d, b_by_name.get(key), dec)

        emitted.add(key)
        merged_defs.append(copy.deepcopy(d))

    # --- 5. Append B-only definitions (not in A, not rename targets) ------
    rename_targets = set(all_renames.values())
    for d in doc_b.definitions:
        name = _definition_name(d)
        key = name if name is not None else _SCHEMA_SENTINEL
        if key in emitted:
            continue
        if name is not None and name in rename_targets:
            continue
        emitted.add(key)
        merged_defs.append(copy.deepcopy(d))

    # --- 6. Emit B versions of renamed types -------------------------------
    for old_name, new_name in sorted(all_renames.items()):
        if new_name in b_by_name and new_name not in emitted:
            emitted.add(new_name)
            merged_defs.append(copy.deepcopy(b_by_name[new_name]))

    # --- 7. Build the final document and serialize -------------------------
    merged_doc = DocumentNode(definitions=tuple(merged_defs))
    merged_doc = _apply_renames_ast(merged_doc, all_renames)
    return _gql_print_ast(merged_doc)


# ── compatibility route ──────────────────────────────────────────────────

def parse_decisions(text: str, a_text: Optional[str] = None,
                    b_text: Optional[str] = None):
    """Deprecated shim. Structured v1 JSON only; prose is rejected.

    Kept so an existing caller importing this name gets a *clear* rejection
    instead of a silently wrong merge. New code should use
    :mod:`merge_decisions` directly.
    """
    from merge_decisions import parse_decisions as _parse

    return _parse(text, a_text, b_text)
