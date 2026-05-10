# Small synthetic example

This directory contains a small synthetic 1C configuration dump pair:

- `cf/` - base configuration dump;
- `cfu/` - extension dump.

Run:

```bash
python -m cfmerge merge \
  --cf examples/small/cf \
  --cfu examples/small/cfu \
  --out examples/small/merged_cf \
  --force \
  --report examples/small/merge-report.json \
  --write-human-report examples/small/merge-report.txt \
  --validate-xml \
  --validate-bsl
```

Generated output and reports are ignored by Git.
