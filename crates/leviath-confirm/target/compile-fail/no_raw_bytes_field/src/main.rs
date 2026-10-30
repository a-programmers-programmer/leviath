
fn main() {
    let value = leviath_confirm::Confidential::new(leviath_confirm::ValueId::new(1), 1, "sk-live-secret");
    let _ = &value.bytes;
}
