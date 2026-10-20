# Sample Outputs

- `accepted-example.json` shows the compact runtime artifact: source vintage, eight-field record, evidence, and one processing summary. Successful artifacts intentionally omit internal attempt traces.
- `failed-eval-example.json` is an actual 2024-01 mismatch from the preserved pre-fix baseline. It records the failure that motivated the general reference-month rule; the current eval now passes that case.
- `quarantine-example.json` is a synthetic rejected candidate. It retains the rejected output, validation errors, and extraction payload needed for diagnosis.
- The complete first baseline and final predictions remain under `eval/`.
