# Analyse

Goal: every analysed requirement is proven by a study that passes, and up to date.

1. For each analysed requirement without a study, create one (`factory tasks` lists the
   tasks and their parameters): `verifies = [...]`, a metric named like the criterion.
2. Run (`factory run <key> -w`, or in the background for long runs) and read the results.
3. When a requirement fails, say why, and propose the change (design, parameter, or a
   decision to relax the requirement). Never edit a criterion to make it pass.
4. Keep studies fresh: a changed input makes them stale; rerun them.
5. Include sanity checks and assumptions in each study's description.
