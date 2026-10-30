
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
