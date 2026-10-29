# Examples

`nano-todo/` is a committed reference run. It was generated deterministically
by `build_nano_todo.py`, not by an LLM. It proves the engine, schemas, gates,
task lifecycle, traceability and metrics.

Rebuild it:

```bash
python examples/build_nano_todo.py
```

Verify it:

```bash
FACTORY_ROOT="$PWD/examples/nano-todo" \
FACTORY_RUNS_ROOT="$PWD/examples" \
factory verify --run-id nano-todo --stage s9_learn --require-gate
```

The example is a reference, not evidence of model autonomy. A real run uses the
same work orders with a host runtime; its artifacts and journal are not
committed (see `.gitignore`).
