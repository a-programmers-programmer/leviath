
fn main() {
    // If this compiled, `Confidential::reveal` would be trivially bypassable.
    let _ = leviath_confirm::Grant { };
}
