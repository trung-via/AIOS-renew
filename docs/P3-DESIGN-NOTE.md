P3 correction-lineage design note.

Canonical P2 base: e37817bce8335e6a158e9aedcae6f90867751c02.
P2 is sufficiently complete after extracting Correction Preflight, Unified State, and Unified Human Surface.

P3 goal: preserve unresolved sibling findings across narrow correction lineage without rediscovery, renaming, ranking, batching, or Runtime selection authority.

Published sequence:
- P3A / TASK-097: exact prospective REMEDIATION predecessor identity, published at 9cb95eb6553131800ff2cb89728147614de2b79d.
- P3B / TASK-098: deterministic outstanding-finding frontier plus publication PASS-and-empty-frontier gate, published at 4df6a12779e254cfe07b5f54de0d89955c321054.

P3C revision history:
- TASK-099 revision 1 implemented the first read-only outstanding-finding surface. RUN-099-001 produced candidate 991de3e551c6c0ba41edbc10768ac713d8f8ddc1. PRIMARY REVIEW-099-001 found F1 (prospective predecessor validation bypass) and F2 (unbounded outstanding-finding exposure). RUN-099-002 / REVIEW-099-002 resolved F1 at 5d96ad13213f1b842af5a44c13b242ffb2cdffba. Publication correctly failed closed because F2 remained outstanding. Revision-1 lineage remains immutable and is not published or treated as completed.
- The revision-1 non-goal excluded cumulative sibling-correction execution. That exclusion became a material contract blocker: current REMEDIATION execution binds Run.base_sha and historical workspace to REMEDIATION.reviewed_sha, so a later sibling finding from the older PRIMARY review can execute from the old candidate and discard an already reviewed sibling correction.
- Creating a separate P3C2 task while revision 1 remains open would advance main onto a sibling implementation branch and manufacture an avoidable self-hosting integration problem. Because cumulative execution-base semantics are new intent relative to revision 1, the canonical continuation is TASK-099 revision 2 rather than disguising P3C2 as another revision-1 FIX.

TASK-099 revision 2 therefore completes P3C as one prospective contract:
- P3A semantic predecessor identity remains immutable and continues to identify the original selected finding.
- Runtime separately binds an exact cumulative execution base representing the candidate on which the next REMEDIATION actually starts.
- Previous reviewed corrections must remain ancestors of later sibling candidates.
- current main must already be contained by the cumulative base; divergence fails closed as integration-required and is never auto-merged or rebased.
- completion/scope authority for prospective cumulative remediation is measured from the execution base, while semantic REVIEW/REMEDIATION validation remains bound to the original predecessor.
- publication independently proves semantic predecessor, cumulative base, candidate ancestry, and empty P3B frontier.
- outstanding-finding exposure is fixed-bounded and never truncates or implies priority.

Historical artifacts remain immutable. Runtime must never auto-rank or auto-select outstanding findings. Brain/Human selection remains explicit. No retry, reroute, fallback, automatic integration, or autonomous correction loop is introduced. P3 is complete only after TASK-099 revision 2 is semantically reviewed PASS and published.
