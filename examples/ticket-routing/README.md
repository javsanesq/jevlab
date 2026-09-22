# Ticket routing

A project-owned Choice design with three synthetic tuning cases and three separate
holdout cases. This tiny dataset demonstrates the workflow; it is not a benchmark
or sufficient evidence for deployment. No recorded answers are included.

From this directory:

```sh
jevlab templates edit ./decision.yaml
jevlab eval plan ./decision.yaml ./cases.jsonl
jevlab export ./decision.yaml --output decision.py
```

These commands do not call an API. See the [project workflow](../../docs/PROJECTS.md)
for real evaluation, baseline comparison, and held-out threshold verification.
Generated Python and evaluation evidence are yours to inspect before committing.
