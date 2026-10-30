
fn main() {
    fn reveal(grant: leviath_confirm::Grant) {
        let _duplicate = grant.clone();
    }
    let _ = reveal;
}
