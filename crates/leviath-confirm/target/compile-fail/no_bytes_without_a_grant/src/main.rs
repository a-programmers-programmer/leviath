
fn main() {
    let value = leviath_confirm::Confidential::new(leviath_confirm::ValueId::new(1), 1, "sk-live-secret");
    // The whole point of the type. If this ever compiles, the gate is decorative.
    let _ = value.as_str();
}
