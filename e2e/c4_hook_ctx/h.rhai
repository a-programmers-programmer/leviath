fn before_inference(ctx) {
    print("c4 iter=" + ctx.iterations + " stage_iter=" + ctx.stage_iterations + " elapsed=" + ctx.elapsed_secs + " cost=" + ctx.cost_usd + " attempt=" + ctx.attempt);
    #{ action: "allow" }
}

fn on_terminal(ctx) {
    print("c4 iter=" + ctx.iterations + " stage_iter=" + ctx.stage_iterations + " elapsed=" + ctx.elapsed_secs + " cost=" + ctx.cost_usd + " attempt=" + ctx.attempt);
    #{ action: "allow" }
}
