# Contributing

Keep the product intentionally small: session state, project, and conversation title belong in scope; terminal orchestration, transcript dashboards, remote services, and prompt analytics do not.

Run before submitting a change:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src
```

Changes to hook configuration must preserve unrelated user hooks and include regression tests.
