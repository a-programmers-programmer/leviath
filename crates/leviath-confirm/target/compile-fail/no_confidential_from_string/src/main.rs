
fn main() {
    // A generated client's obvious move. There is no such impl, and there must
    // not be: a sensitive value is created where the secret lives.
    let value: leviath_confirm::Confidential = "sk-live-secret".into();
    let _ = value.len();
}
