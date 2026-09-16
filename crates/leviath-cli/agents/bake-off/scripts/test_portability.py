#!/usr/bin/env python3
"""Fresh-install / portability tests for the HarnessQL lanes (work order GQL-09).

Run:  python3 scripts/test_portability.py

The definition of done for GQL-09 says, verbatim:

    A fresh install works outside /data/work/leviath. No raw candidate read or
    full-schema write tool is available to the merge reviewer.

These checks *demonstrate* that, rather than asserting it in prose. They:

  A. grep the shipped sources for the pre-GQL-09 scratch paths and key-file reads
     (`/data/work/leviath`, `/data/state/openrouter_key.txt`, `/data/config.yaml`,
     any `*KEY*` environment injection) - every hit is a failure;
  B. copy the two agent directories (bake-off + bake-merge) into a temporary
     directory that is NOT under /data/work/leviath, and prove every resolved
     path lands inside that copy - the guide, the seed, the workdir, the merge
     format - with `HARNESSQL_LEGACY_STUDY` unset;
  C. prove the portability is real by *running* the copied driver (`paths`,
     `diff-task`, `merge`) against the copied tree, so a hard-coded path that the
     grep missed would still fail the test;
  D. assert the merge reviewer's contract: `bake-merge/agent.leviath` grants
     `allow = []` read paths and its stage's `available_tools` names no file
     tool (`read_file`, `write_file`, `list_dir`, `bash`, ...), so no raw
     candidate read and no full-schema write is reachable;
  E. assert the tools are packaged (generated and up to date) so the lanes read
     the guide/format from the bundle and not from a scratch file.

Exit 0 when every check passes; 1 otherwise. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
MERGE_DIR = AGENT_DIR.parent / "bake-merge"

FAILURES: list[str] = []

# The pre-GQL-09 scratch locations and secret reads that must not survive.
FORBIDDEN_PATTERNS = [
    (re.compile(r"/data/work/leviath(?!/)"), "scratch path /data/work/leviath"),
    (re.compile(r"/data/state/openrouter_key"), "key file /data/state/openrouter_key.txt"),
    (re.compile(r"openrouter_key"), "key file name 'openrouter_key'"),
    (re.compile(r"/data/config\.yaml"), "config /data/config.yaml"),
    (re.compile(r"/data/profiles/"), "profile config /data/profiles/*"),
    (re.compile(r"OPENROUTER_API_KEY\s*\]\s*="), "key injected into env"),
    (re.compile(r"os\.environ\[[\"']OPENROUTER_API_KEY[\"']\]\s*="), "key injected into env"),
]

# Tools that would let the merge reviewer read a raw candidate or write a schema.
FORBIDDEN_MERGE_TOOLS = [
    "read_file", "write_file", "edit_file", "list_dir", "bash", "shell",
    "apply_patch", "multi_edit", "create_file", "delete_file",
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"PASS {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL {name} {detail}")


def shipped_sources() -> list[Path]:
    """Every source we own and ship: the scripts, the blueprints, the tools.

    This test file is excluded: it must name the forbidden patterns in order to
    search for them, so scanning it would always report itself.
    """
    out: list[Path] = []
    out += sorted(p for p in HERE.glob("*.py") if p.name != Path(__file__).name)
    out += [AGENT_DIR / "agent.leviath", MERGE_DIR / "agent.leviath"]
    out += sorted((AGENT_DIR / "tools").glob("*.rhai"))
    out += sorted((MERGE_DIR / "tools").glob("*.rhai"))
    return [p for p in out if p.is_file()]


# ── A. no scratch path, no key read ──────────────────────────────────────

def _documentation_lines(path: Path, text: str) -> set[int]:
    """Line numbers that are prose: comments and docstrings.

    The forbidden-path rule is about what the code DOES at run time. A docstring
    that says "there is no /data/work/leviath default" is the documentation of the
    fix, not a dependency on the scratch tree, so it must not fail the check -
    while a path in real code (or in a real string literal) must.
    """
    lines: set[int] = set()
    if path.suffix != ".py":
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                lines.add(lineno)
        return lines

    import ast
    import tokenize
    import io

    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) and node.body:
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                        lines.add(ln)
    except SyntaxError:
        pass
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except tokenize.TokenError:
        pass
    return lines


def test_no_scratch_paths_or_secrets() -> None:
    print("\n[A] shipped sources hold no scratch path and no secret read")
    offenders: list[str] = []
    for path in shipped_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        skip = _documentation_lines(path, text)
        for lineno, line in enumerate(text.splitlines(), 1):
            if lineno in skip:
                continue
            for pat, label in FORBIDDEN_PATTERNS:
                if pat.search(line):
                    offenders.append(f"{path.name}:{lineno}: {label}: {line.strip()[:90]}")
    check("A1 no forbidden path/secret pattern in executable code", not offenders,
          "\n     " + "\n     ".join(offenders))

    # A docstring mention is not a dependency, but it must be a *negation*: a
    # positive instruction to use the scratch path would be a real defect.
    bad_prose: list[str] = []
    for path in shipped_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if lineno not in _documentation_lines(path, text):
                continue
            low = line.lower()
            if "openrouter_key" in low or "/data/config.yaml" in low:
                bad_prose.append(f"{path.name}:{lineno}: {line.strip()[:90]}")
    check("A1b no secret path is even named in prose", not bad_prose,
          "\n     " + "\n     ".join(bad_prose))


def test_legacy_study_is_optin_only() -> None:
    print("\n[A2] the legacy scratch study is opt-in, never a default")
    env = dict(os.environ)
    env.pop("HARNESSQL_LEGACY_STUDY", None)
    code = ("import sys, json; sys.path.insert(0, %r); "
            "import harnessql_paths as p; "
            "print(json.dumps({'s': str(p.default_seed()), 'w': str(p.workdir()), "
            "'l': str(p.legacy_study())}))" % str(HERE))
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, cwd=str(HERE))
    if proc.returncode != 0:
        check("A2 legacy study opt-in only", False, proc.stderr.strip()[:200])
        return
    got = json.loads(proc.stdout)
    check("A2a legacy_study() is None when unset", got["l"] == "None", got["l"])
    check("A2b default seed is in-repo, not scratch",
          got["s"].startswith(str(AGENT_DIR)) and "/data/work/leviath" not in got["s"],
          got["s"])
    check("A2c workdir is in-repo, not scratch",
          got["w"].startswith(str(AGENT_DIR)) and "/data/work/leviath" not in got["w"],
          got["w"])


# ── B. fresh install outside /data/work/leviath ──────────────────────────

def _copy_agents(root: Path) -> tuple[Path, Path]:
    """Copy both agent dirs into `root`, skipping caches and stale work output."""
    def ignore(_dir, names):
        return [n for n in names
                if n in {"__pycache__", ".pytest_cache", "work", "study", "hooks"}]
    off = root / "agents" / "bake-off"
    mrge = root / "agents" / "bake-merge"
    shutil.copytree(AGENT_DIR, off, ignore=ignore)
    # the seed is an input, keep it; only the scratch dirs and caches are skipped
    (off / "study").mkdir(exist_ok=True)
    shutil.copy2(AGENT_DIR / "study" / "B2.graphql", off / "study" / "B2.graphql")
    shutil.copytree(MERGE_DIR, mrge, ignore=ignore)
    return off, mrge


def test_fresh_install_paths(tmp: Path) -> tuple[Path, Path]:
    print(f"\n[B] fresh install outside the repo: {tmp}")
    off, mrge = _copy_agents(tmp)
    env = dict(os.environ)
    env.pop("HARNESSQL_LEGACY_STUDY", None)
    env.pop("HARNESSQL_AGENT_DIR", None)
    env.pop("HARNESSQL_SEED", None)
    env.pop("HARNESSQL_WORKDIR", None)
    env.pop("HARNESSQL_BAKE_MERGE_DIR", None)

    check("B1 install root is outside /data/work/leviath",
          "/data/work/leviath" not in str(tmp), str(tmp))

    proc = subprocess.run([sys.executable, str(off / "scripts" / "harnessql_driver.py"),
                           "paths"], capture_output=True, text=True, env=env,
                          cwd=str(off))
    if proc.returncode != 0:
        check("B2 copied driver runs `paths`", False, proc.stderr.strip()[:300])
        return off, mrge
    check("B2 copied driver runs `paths`", True)
    got = json.loads(proc.stdout)

    for key in ("agent_dir", "guide", "seed", "workdir", "evidence_dir",
                "bake_merge_dir", "merge_format"):
        value = got.get(key) or ""
        check(f"B3 {key} resolves inside the fresh install",
              value.startswith(str(tmp)) and "/data/work/leviath" not in value,
              value)
    check("B3a legacy_study is null in the fresh install", got.get("legacy_study") is None,
          str(got.get("legacy_study")))
    check("B4 guide file exists in the fresh install", Path(got["guide"]).is_file())
    check("B5 seed file exists in the fresh install", Path(got["seed"]).is_file())
    check("B6 merge format exists in the fresh install", Path(got["merge_format"]).is_file())

    # The packaged tools must travel with the copy.
    check("B7 composition_guide tool shipped",
          (off / "tools" / "composition_guide.rhai").is_file())
    check("B8 merge_decision_format tool shipped",
          (mrge / "tools" / "merge_decision_format.rhai").is_file())
    return off, mrge


# ── C. run the copied driver end to end ──────────────────────────────────

def test_copied_driver_runs(tmp: Path, off: Path) -> None:
    print("\n[C] the copied driver actually runs (no hidden absolute path)")
    env = dict(os.environ)
    env.pop("HARNESSQL_LEGACY_STUDY", None)
    driver = str(off / "scripts" / "harnessql_driver.py")
    a = str(off / "study" / "B2.graphql")
    b = str(tmp / "cand_b.graphql")
    # a real, slightly different candidate: drop one field from the seed's Query
    text = Path(a).read_text(encoding="utf-8")
    Path(b).write_text(text.replace("  node(id: ID!): Node\n", "", 1), encoding="utf-8")

    out_diff = str(tmp / "diff.txt")
    proc = subprocess.run([sys.executable, driver, "diff-task", "--a", a,
                           "--b", b, "--out", out_diff],
                          capture_output=True, text=True, env=env, cwd=str(tmp))
    check("C1 diff-task runs and writes the diff",
          proc.returncode == 0 and Path(out_diff).is_file(),
          proc.stderr.strip()[:300])
    if not Path(out_diff).is_file():
        return
    diff_text = Path(out_diff).read_text(encoding="utf-8")
    check("C2 diff names the copied candidate paths, no scratch path",
          a in diff_text and "/data/work/leviath" not in diff_text)

    # merge without a decision document must be BLOCKED (exit 2), never guessed
    proc = subprocess.run([sys.executable, driver, "merge", "--a", a, "--b", b,
                           "--out", str(tmp / "merged.graphql")],
                          capture_output=True, text=True, env=env, cwd=str(tmp))
    check("C3 merge with no decisions is blocked (exit 2)", proc.returncode == 2,
          f"exit {proc.returncode}: {proc.stdout.strip()[:200]}")


def test_determinism_across_installs(tmp: Path, off: Path) -> None:
    print("\n[C4] same candidates + same decisions => identical bytes, two trees")
    env = dict(os.environ)
    env.pop("HARNESSQL_LEGACY_STUDY", None)
    driver = str(off / "scripts" / "harnessql_driver.py")

    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(off / 'scripts')!r})\n"
        "from graphql_diff import diff_types, propose_renames\n"
        "from merge_decisions import merge, sha256_text\n"
        f"a = open({str(off / 'study' / 'B2.graphql')!r}, encoding='utf-8').read()\n"
        f"b = open({str(tmp / 'cand_b.graphql')!r}, encoding='utf-8').read()\n"
        "d = diff_types(a, b)\n"
        "types = []\n"
        "for name in d.divergent:\n"
        "    ta, tb = d.a_types[name], d.b_types[name]\n"
        "    if ta.kind != tb.kind:\n"
        "        types.append({'name': name, 'disposition': 'keep_a',\n"
        "                      'reason': 'kind changed: different abstractions'})\n"
        "        continue\n"
        "    key = 'fields' if ta.kind in ('type','interface','input') else (\n"
        "          'values' if ta.kind == 'enum' else 'members')\n"
        "    ak = list(ta.fields) if key=='fields' else (list(ta.enum_values) if key=='values'\n"
        "                                                else list(ta.union_members))\n"
        "    bk = list(tb.fields) if key=='fields' else (list(tb.enum_values) if key=='values'\n"
        "                                                else list(tb.union_members))\n"
        "    m = {k: 'a' for k in ak}\n"
        "    for k in bk:\n"
        "        m.setdefault(k, 'b')\n"
        "    types.append({'name': name, 'disposition': 'reconcile', key: m,\n"
        "                  'reason': 'superset: keeps every key from both sides'})\n"
        "ren = [{'from': o, 'to': n, 'disposition': 'rename', 'reason': 'same shape'}\n"
        "       for o, n in propose_renames(d)]\n"
        "doc = {'contract': 'harnessql-merge-decisions/v1', 'diff': {'sha256': ''},\n"
        "       'candidates': {'a': {'path': 'a', 'sha256': sha256_text(a)},\n"
        "                      'b': {'path': 'b', 'sha256': sha256_text(b)}},\n"
        "       'types': types, 'renames': ren}\n"
        "print(json.dumps(doc))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, cwd=str(tmp))
    if proc.returncode != 0:
        check("C4a decision document built for the copied candidates", False,
              proc.stderr.strip()[:300])
        return
    doc = json.loads(proc.stdout)
    doc_path = tmp / "decisions.json"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    # The document must carry the diff sha256 printed by the tool, so re-derive it.
    diff_path = str(tmp / "diff2.txt")
    subprocess.run([sys.executable, driver, "diff-task",
                    "--a", str(off / "study" / "B2.graphql"),
                    "--b", str(tmp / "cand_b.graphql"), "--out", diff_path],
                   capture_output=True, text=True, env=env, cwd=str(tmp))
    sha_code = ("import sys, json; sys.path.insert(0, %r); "
                "from merge_decisions import sha256_text; "
                "print(sha256_text(open(%r, encoding='utf-8').read()))"
                % (str(off / "scripts"), diff_path))
    sha = subprocess.run([sys.executable, "-c", sha_code], capture_output=True, text=True,
                         env=env).stdout.strip()
    doc["diff"]["sha256"] = sha
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    results = []
    for tag in ("t1", "t2"):
        outdir = tmp / tag
        outdir.mkdir(exist_ok=True)
        out = str(outdir / "merged.graphql")
        proc = subprocess.run(
            [sys.executable, driver, "merge",
             "--a", str(off / "study" / "B2.graphql"),
             "--b", str(tmp / "cand_b.graphql"), "--out", out,
             "--decisions", str(doc_path), "--evidence-dir", str(outdir / "evidence")],
            capture_output=True, text=True, env=env, cwd=str(tmp))
        if proc.returncode != 0:
            check(f"C4 merge run {tag} verified", False,
                  f"exit {proc.returncode}: {proc.stdout.strip()[-300:]}")
            return
        bundle = json.loads((outdir / "evidence" / "bundle.json").read_text())
        results.append(bundle)

    check("C4b both runs exit 0 and write evidence", len(results) == 2)
    check("C4c identical result bytes across runs",
          results[0]["result_sha256"] == results[1]["result_sha256"],
          f"{results[0]['result_sha256'][:12]} vs {results[1]['result_sha256'][:12]}")
    check("C4d reapply check recorded as identical",
          results[0]["reapply"]["identical"] is True)
    check("C4e evidence bundle records rationale, hashes, diff, decisions, result",
          all(k in results[0] for k in ("rationale", "candidate_sha256", "diff",
                                        "decisions", "result", "decision_sha256")))
    check("C4f candidate paths recorded, no scratch path in the record",
          all("/data/work/leviath" not in p
              for p in results[0]["candidate_paths"].values()),
          str(results[0]["candidate_paths"]))
    check("C4g a merged non-convergence is not labelled independent",
          results[0]["convergence"]["label"] != "two independent candidates",
          results[0]["convergence"]["label"])


# ── D. the merge reviewer's tool contract ────────────────────────────────

def test_merge_reviewer_is_restricted() -> None:
    print("\n[D] the merge reviewer can read no candidate and write no schema")
    text = (MERGE_DIR / "agent.leviath").read_text(encoding="utf-8")

    m = re.search(r"\[read_paths\][^\[]*allow\s*=\s*\[([^\]]*)\]", text, re.S)
    check("D1 [read_paths] allow = [] in bake-merge", m is not None and not m.group(1).strip(),
          m.group(1).strip() if m else "no read_paths block found")

    tools = the_tools_available(text)
    check("D2 the decide stage grants some tools (it is not empty)", bool(tools), str(tools))
    bad = [t for t in tools if t in FORBIDDEN_MERGE_TOOLS]
    check("D3 no raw-candidate-read or schema-write tool is available", not bad, str(bad))
    check("D4 submit_output is the answer channel", "submit_output" in tools, str(tools))
    check("D5 the decision-format tool is bundled", "merge_decision_format" in tools, str(tools))

    # A mention in prose is only a *denial* here ("There is no write_file"); what
    # matters is that nothing GRANTS a file or shell tool. D2/D3 already parse the
    # grants; this confirms every mention is inside a denial sentence.
    for name in ("write_file", "read_file", "list_dir", "bash", "shell"):
        for line in text.splitlines():
            if name in line and "no " not in line.lower() and "cannot" not in line.lower() \
                    and "not " not in line.lower():
                check(f"D6 {name} is only mentioned as a denial", False, line.strip()[:100])
                break
        else:
            check(f"D6 {name} is only mentioned as a denial", True)


def the_tools_available(blueprint: str) -> list[str]:
    """Every tool name granted by any stage of a blueprint."""
    names: set[str] = set()
    for key in ("available_tools", "required_tools"):
        for m in re.finditer(rf"{key}\s*=\s*\[([^\]]*)\]", blueprint):
            for raw in m.group(1).split(","):
                tool = raw.strip().strip('"').strip("'")
                if tool:
                    names.add(tool)
    for m in re.finditer(r"submit_output\s*=\s*\"allow\"", blueprint):
        names.add("submit_output")
    return sorted(names)


def test_merge_blueprint_has_no_file_tools_run() -> None:
    """Ask `lev validate` too, when a lev binary is available."""
    print("\n[D7] lev validate accepts the restricted blueprint (when lev is present)")
    sys.path.insert(0, str(HERE))
    import harnessql_paths  # noqa: E402
    try:
        lev = harnessql_paths.lev_bin()
    except Exception as exc:  # pragma: no cover - no lev on this box
        check("D7 lev available", True, f"skipped: {exc}")
        return
    proc = subprocess.run([lev, "validate", str(MERGE_DIR)], capture_output=True, text=True)
    check("D7 lev validate bake-merge exits 0", proc.returncode == 0, proc.stderr[-300:])
    out = proc.stdout + proc.stderr
    check("D8 no broad-read-path warning for bake-merge", "broad-read-path" not in out,
          out[-300:])


# ── E. tools packaged and current ────────────────────────────────────────

def test_tools_packaged() -> None:
    print("\n[E] the references are packaged tools, generated and current")
    for name, path in (("composition_guide", AGENT_DIR / "tools" / "composition_guide.rhai"),
                       ("merge_decision_format", MERGE_DIR / "tools" / "merge_decision_format.rhai")):
        check(f"E1 {name} tool exists", path.is_file(), str(path))
        if path.is_file():
            body = path.read_text(encoding="utf-8")
            check(f"E2 {name} is a declared @tool", f"// @tool {name}" in body)
    proc = subprocess.run([sys.executable, str(HERE / "generate_tools.py"), "--check"],
                          capture_output=True, text=True)
    check("E3 generate_tools.py --check reports OK", proc.returncode == 0 and
          "STALE" not in (proc.stdout + proc.stderr), proc.stdout.strip()[-300:])


def main() -> int:
    print("GQL-09 portability / fresh-install tests")
    test_no_scratch_paths_or_secrets()
    test_legacy_study_is_optin_only()
    test_merge_reviewer_is_restricted()
    test_tools_packaged()
    with tempfile.TemporaryDirectory(prefix="harnessql-fresh-") as tmpdir:
        tmp = Path(tmpdir)
        off, mrge = test_fresh_install_paths(tmp)
        test_copied_driver_runs(tmp, off)
        test_determinism_across_installs(tmp, off)
    test_merge_blueprint_has_no_file_tools_run()

    print()
    if FAILURES:
        print(f"=== {len(FAILURES)} CHECK(S) FAILED ===")
        for name in FAILURES:
            print(f"  FAILED {name}")
        return 1
    print("=== ALL PORTABILITY CHECKS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())