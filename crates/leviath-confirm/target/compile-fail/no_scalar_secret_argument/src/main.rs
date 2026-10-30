
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
