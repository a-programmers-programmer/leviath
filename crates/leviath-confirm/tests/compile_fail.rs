//! Compile-fail checks: the parts of the contract that only the compiler can
//! prove.
//!
//! Every other test in this crate asserts *behaviour*. These assert
//! *expressibility*: that a program which would leak the secret, or forge an
//! approval, does not compile at all. A runtime assertion that `reveal` returned
//! `Err` would pass even if a `pub fn bytes(&self) -> &str` existed next to it.
//!
//! Each case builds a throwaway crate depending on this one by path and asserts
//! that `cargo build` fails, and — more importantly — that it fails for the
//! reason claimed rather than for a typo in the case's own code. A case that
//! merely fails to compile because of an unrelated error would be worthless, so
//! `expect_error_contains` is checked on the compiler output.

use std::path::{Path, PathBuf};
use std::process::Command;

/// One compile-fail case.
struct Case {
    /// What the case is called; also the file's name.
    name: &'static str,
    /// The claim: this program must not compile.
    claim: &'static str,
    /// A substring `rustc` must report. This is the "fails for the right reason"
    /// half — the error codes and messages below are `E0599`/`E0624` (no such
    /// method, or private), `E0616` (private field), `E0451` (private struct
    /// fields), `E0277` (trait not satisfied), `E0308` (type mismatch) and the
    /// bare "cannot construct `Grant`" rustc reports with no code.
    expect: &'static str,
    /// The program.
    source: &'static str,
}

const CASES: &[Case] = &[
    Case {
        name: "no_bytes_without_a_grant",
        claim: "there is no way to read a Confidential's bytes by calling a method on it",
        expect: "no method named",
        source: r#"
fn main() {
    let value = leviath_confirm::Confidential::new(leviath_confirm::ValueId::new(1), 1, "sk-live-secret");
    // The whole point of the type. If this ever compiles, the gate is decorative.
    let _ = value.as_str();
}
"#,
    },
    Case {
        name: "no_raw_bytes_field",
        claim: "the bytes are not a reachable field",
        expect: "field",
        source: r#"
fn main() {
    let value = leviath_confirm::Confidential::new(leviath_confirm::ValueId::new(1), 1, "sk-live-secret");
    let _ = &value.bytes;
}
"#,
    },
    Case {
        name: "no_confidential_from_string",
        claim: "a Confidential cannot be produced by a From<&str>/From<String> conversion",
        expect: "E0277",
        source: r#"
fn main() {
    // A generated client's obvious move. There is no such impl, and there must
    // not be: a sensitive value is created where the secret lives.
    let value: leviath_confirm::Confidential = "sk-live-secret".into();
    let _ = value.len();
}
"#,
    },
    Case {
        name: "no_request_id_constructor",
        claim: "a RequestId cannot be minted outside the crate, so a handle cannot be forged",
        expect: "E0624",
        source: r#"
fn main() {
    // A plausible-looking handle is worthless: it has to be one this gate minted.
    let _ = leviath_confirm::RequestId::mint(
        &leviath_confirm::PrincipalId::new("alice").unwrap(),
        &leviath_confirm::Nonce::new("nonce-aaaaaaaaaaaa").unwrap(),
        leviath_confirm::Confidential::new(leviath_confirm::ValueId::new(1), 1, "x").binding(),
        &leviath_confirm::Intent::new(
            leviath_confirm::ActionId::new("a.b").unwrap(),
            leviath_confirm::TargetRef::new("k", "i").unwrap(),
            leviath_confirm::Scope::empty(),
        ),
    );
}
"#,
    },
    Case {
        name: "no_unverified_principal",
        claim: "a VerifiedPrincipal cannot be built from a bare string or a struct literal",
        expect: "E0451",
        source: r#"
fn main() {
    // `PrincipalId::new` is the only way to name a principal, and it is not
    // enough: what is missing is the ingress assertion, which is a function the
    // caller has to decide to call.
    let principal = leviath_confirm::VerifiedPrincipal {
        id: leviath_confirm::PrincipalId::new("alice").unwrap(),
        role: leviath_confirm::PrincipalRole::Human,
    };
    let _ = principal.role();
}
"#,
    },
    Case {
        name: "no_grant_construction",
        claim: "a Grant cannot be constructed, so a caller cannot mint its own authority",
        expect: "cannot construct `Grant`",
        source: r#"
fn main() {
    // If this compiled, `Confidential::reveal` would be trivially bypassable.
    let _ = leviath_confirm::Grant { };
}
"#,
    },
    Case {
        name: "no_grant_deserialize",
        claim: "a Grant cannot be restored from bytes, so it cannot be stashed or replayed",
        expect: "E0277",
        source: r#"
fn main() {
    let _: leviath_confirm::Grant = serde_json::from_str("{}").unwrap();
}
"#,
    },
    Case {
        name: "no_grant_clone",
        claim: "a Grant cannot be duplicated, so one approval is one read",
        expect: "no method named `clone`",
        source: r#"
fn main() {
    fn reveal(grant: leviath_confirm::Grant) {
        let _duplicate = grant.clone();
    }
    let _ = reveal;
}
"#,
    },
    Case {
        name: "no_scalar_secret_argument",
        claim: "a gate operation takes a Confidential, never a bare secret string",
        expect: "E0308",
        source: r#"
fn main() {
    let mut gate = leviath_confirm::Gate::<leviath_confirm::GatedValue<leviath_confirm::HumanGateApproval>>::open(0);
    // `wanted` and the value cannot be swapped, and the secret cannot be handed
    // to the gate as a string: the gate does not know the bytes, which is why
    // compromising it does not disclose them.
    let _ = gate.with_confirmed_value(
        &leviath_confirm::VerifiedPrincipal::from_verified_ingress(
            leviath_confirm::PrincipalId::new("alice").unwrap(),
            leviath_confirm::PrincipalRole::Human,
        ),
        &leviath_confirm::RequestId::ledger(),
        &leviath_confirm::Intent::new(
            leviath_confirm::ActionId::new("a.b").unwrap(),
            leviath_confirm::TargetRef::new("k", "i").unwrap(),
            leviath_confirm::Scope::empty(),
        ),
        "sk-live-secret",
        0,
        |_, _| (),
    );
}
"#,
    },
];

// Note on `Confidential`: it *has* a `Deserialize` impl, and that impl always
// returns an error rather than being absent. The absence of an impl would also
// be a compile error, but it would make a `#[derive(Deserialize)]` on an
// enclosing type fail with "trait not satisfied" — a message that does not say
// why. Refusing inside the impl keeps the failure legible and, more importantly,
// keeps it *assertable*: see
// `dod_a_secret_cannot_be_reconstructed_from_a_payload` in `gate_dod.rs`, which
// checks the message. So this one property is proved at run time, deliberately,
// and the reason is recorded here rather than left to a reader to wonder.

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// Build `case` in a scratch crate and return `(compiled, stderr)`.
fn try_compile(case: &Case, scratch: &Path) -> (bool, String) {
    let dir = scratch.join(case.name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("src")).expect("scratch dir");

    // The case's crate depends on this one by path, so it proves the guarantee
    // for a *separate* crate — which is what a generated client and a GraphQL
    // resolver module actually are.
    let manifest = format!(
        "[workspace]\n[package]\nname = \"{}\"\nversion = \"0.0.0\"\nedition = \"2024\"\n\n\
         [dependencies]\nleviath-confirm = {{ path = \"{}\" }}\nserde_json = \"1\"\n",
        case.name,
        workspace_root().display()
    );
    std::fs::write(dir.join("Cargo.toml"), manifest).expect("manifest");
    std::fs::write(dir.join("src/main.rs"), case.source).expect("case source");

    let output = Command::new(env!("CARGO"))
        .arg("build")
        // One shared target dir, so the nine cases compile the dependency tree
        // once instead of nine times.
        .env(
            "CARGO_TARGET_DIR",
            workspace_root().join("target/compile-fail-target"),
        )
        .current_dir(&dir)
        .output()
        .expect("run cargo");
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    (output.status.success(), stderr)
}

#[test]
fn the_contract_is_enforced_by_the_compiler_not_by_a_comment() {
    // Kept inside this crate's own `target/`, which is already ignored: these
    // are throwaway crates, not deliverables.
    let scratch = workspace_root().join("target/compile-fail");
    std::fs::create_dir_all(&scratch).expect("scratch root");

    let mut proved = Vec::new();
    for case in CASES {
        let (compiled, stderr) = try_compile(case, &scratch);
        if compiled {
            panic!(
                "case `{}` COMPILED, but it must not: {}\n\
                 This is a real hole in the gate, not a broken test.\n--- compiler output ---\n{}",
                case.name, case.claim, stderr
            );
        }
        assert!(
            stderr.contains(case.expect),
            "case `{}` failed to compile, but not for the claimed reason ({}).\n\
             Expected the compiler output to contain `{}`.\n--- compiler output ---\n{}",
            case.name,
            case.claim,
            case.expect,
            stderr
        );
        proved.push(case.name);
    }

    // Named here so a reader of a passing run sees exactly what was proved.
    assert_eq!(proved.len(), CASES.len());
    eprintln!("compile-fail cases proved: {}", proved.join(", "));
}
