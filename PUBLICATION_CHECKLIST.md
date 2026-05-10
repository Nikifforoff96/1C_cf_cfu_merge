# Publication checklist

Use this checklist when creating the public GitHub repository.

1. Create a new empty repository without importing local history.
2. Copy only the public source set:
   - `cfmerge/`
   - `tests/`
   - `examples/`
   - `tools/`
   - `.github/`
   - `README.md`
   - `LICENSE`
   - `pyproject.toml`
   - `.gitignore`
   - `.v8-project.example.json`
3. Do not copy local agent instructions, private validation dumps, generated outputs, test infobases, binary configuration dumps, or merge reports.
4. Run:

```bash
python -m pip install -e .
python -m pytest -q
python -m cfmerge --help
```

5. Run a local scan for private infrastructure values using the private marker list kept outside the repository.
6. Open the repository on GitHub and verify that Actions pass.
