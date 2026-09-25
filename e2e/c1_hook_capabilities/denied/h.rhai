fn on_stage_enter(ctx) {
    let out = shell("echo c1-ok");
    print("c1 hook shell output: " + out);
    #{ action: "allow" }
}
