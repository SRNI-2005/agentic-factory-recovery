# Task B10 Report: CLI workbook import and template export

## Status: COMPLETE

## Summary
Added two new CLI commands to `coe/cli.py`:
- `import workbook --path P [--name N]` — imports a user-authored Excel workbook via `apply_workbook()`, forking `factory_demo_01`
- `template export --instance I [--out PATH]` — exports an instance's editable domains to xlsx via `export_workbook()`

## Changes

### `coe/cli.py` (+46 lines)
- **Parser**: Added `workbook` subcommand under `import` with `--path` (required) and `--name` (optional, defaults to Meta.target_name)
- **Parser**: Added `template` top-level group with `export` subcommand, `--instance` (required) and `--out` (default: `data/templates/factory_workbook.xlsx`)
- **Dispatch**: `import workbook` reads bytes, resolves `factory_demo_01` parent, calls `apply_workbook()`, converts `WorkbookRejected` → `SystemExit`, prints instance name
- **Dispatch**: `template export` resolves instance via `_instance_or_die()`, calls `export_workbook()`, writes to `--out` with `mkdir(parents=True)`, prints output path

### `tests/test_cli_workbook.py` (new, 8 tests)
- `TestImportWorkbook` (4): missing path, with name, default name from meta, rejects bad workbook
- `TestTemplateExport` (4): missing instance, creates file, default path, unknown instance

## Test Results
- 8/8 new tests pass
- Full quick-gate suite: 400 passed, 1 skipped, 1 pre-existing failure (`test_preflight_fails_fast` — missing GOOGLE_API_KEY env var, unrelated)

## Commit
```
feat(cli): add import workbook and template export commands (B10)
```

## Concerns
- The `import workbook` command hardcodes `factory_demo_01` as the parent instance. This is per the brief spec; a future task could parameterize it.
- The generated `data/templates/factory_workbook.xlsx` from test runs may need to be gitignored or added to the repo (currently tracked by git).

## Report Path
`.superpowers/sdd/task-B10-report.md`
