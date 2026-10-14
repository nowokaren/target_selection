# Priority decision flow

See [`decision_flow.md`](decision_flow.md) for the rules and category definitions. Regenerate the figure with:

```bash
python docs/planning_pipeline/priority_flow/generate_decision_flow.py
```

The script requires the Graphviz `dot` executable and writes `decision_flow.png` next to itself. The diagram captures the current first-pass logic; it does not calculate a score or assign calibrated instrument thresholds.
