# Desk, queue, run and decision requirements

Illustrative GraphQL projection over the existing Desk and claim-queue services.
Those services stay the source of truth. This is not an existing served schema.

- R-file: a desk member files a slip. The slip is enqueued only when its
  delivery lane has a runner, and never for an ask to a person.
- R-browse-slips: a member pages through slips without duplicates in stable
  identity order.
- R-read-slip: a member reads one slip with its derived status and provenance.
- R-queue-read: a member pages claim-queue rows and takes one for a named holder.
- R-renew: a holder extends a live claim so long work stays honestly in progress.
- R-close-run: a holder completes a claimed row after a report, or releases it
  with a reason instead of waiting out the lease.
- R-thread: a member reads a slip thread oldest first.
- R-status: a member reads the derived lifecycle light and its age wording.
- R-stream: a client streams slip-status changes in sequence order.
- R-decide: a human records the answer to a decide slip and it routes onward.

Caller compatibility is the hard requirement. The existing Desk and queue HTTP
callers already send these fields; this schema must not invent a second
vocabulary for them.

- Kind vocabulary: task, review, decide, unblock, look. DECIDE is separate.
- Product: anw, parlour, platform.
- Owner: andy, josh, both.
- Delivery: auto, simple-city, hive. A typo is refused, never silently
  normalised to auto.
- Thread kinds: prompt, report, event, digest, decision, tg-prompt, tg-note.
- Lifecycle lights: idle, sent, working, reported, done.
- Queue states: queued, claimed, released, done, archived.

The claim queue remains the queue-state authority. No state machine, lease
length or reclaim rule is re-implemented in this schema. Planning dependencies
stay in the existing planning tool where they are already used.

No custom scalars, no federation and no model in the contract path.