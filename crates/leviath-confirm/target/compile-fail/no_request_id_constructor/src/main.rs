
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
