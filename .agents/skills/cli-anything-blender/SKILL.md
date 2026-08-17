---
name: cli-anything-blender
description: >-
  Use the repository's cli-anything-blender harness to create, inspect, modify,
  render, preview, animate, or process Blender scenes and FBX files. Trigger
  this skill for 3D scene work in this repository, especially when the task
  needs machine-readable CLI output, reproducible JSON scene edits, headless
  Blender rendering, preview artifacts, live preview sessions, or validated
  FBX processing.
---

# Blender CLI Workflow

Use the repository CLI as the primary interface for Blender scene work. Keep
the JSON scene project as the source of truth, make changes through CLI
commands, and verify the result through JSON inspection and real preview
artifacts when Blender is available.

## Locate and invoke the CLI

Set the harness directory to:

```text
<workspace>/Harness/blender/agent-harness
```

In this workspace, the absolute path is:

```text
H:\WorkSpace\Art-Tools\Harness\blender\agent-harness
```

Prefer the installed entry point when it exists:

```powershell
$cli = 'H:\WorkSpace\Art-Tools\Harness\blender\agent-harness\.venv\Scripts\cli-anything-blender.exe'
& $cli --help
```

Otherwise run the module from the harness directory:

```powershell
Set-Location 'H:\WorkSpace\Art-Tools\Harness\blender\agent-harness'
python -m cli_anything.blender --help
```

When the checkout has moved, locate `cli_anything/blender/blender_cli.py` and
use its `agent-harness` parent instead of assuming the example drive letter.
Do not use the stale `cli.blender_cli` module path shown in older package
examples.

## Operating rules

1. Use absolute paths for project files, render outputs, FBX inputs, FBX outputs,
   preview roots, and generated scripts.
2. Put global flags before the command group. Use `--json` for every command
   whose result will be parsed, for example:

   ```text
   cli-anything-blender --json --project C:\work\scene.blend-cli.json object list
   ```

3. Check the process exit code. On failure, read stderr and the JSON error
   payload before retrying.
4. Query `scene info`, `object list`, `material list`, or the relevant `list`
   command before using an index. Re-query after adding or removing entries;
   indexes are positional and can change.
5. Use `--dry-run` for a proposed mutating command when the user needs a
   preview before writing. For final work, save explicitly with `scene save`.
6. Prefer one-shot commands for automation. Use the REPL only when the user
   explicitly wants an interactive session.
7. Do not edit the JSON project by hand merely to avoid a missing CLI command.
   Inspect the command help and source first; if the capability is genuinely
   absent, report that limitation before choosing a fallback.
8. Treat `--overwrite` and especially `--overwrite-source` as destructive:
   use them only when the requested destination is confirmed and replacement
   is authorized.

Read [references/command-reference.md](references/command-reference.md) when
exact group syntax or less-common options are needed.

## Standard scene workflow

1. Choose an absolute project path outside source files, usually ending in
   `.blend-cli.json`.
2. Create or open the project:

   ```powershell
   & $cli --json scene new --name 'Product' --profile preview --output 'C:\work\product.blend-cli.json'
   # or
   & $cli --json --project 'C:\work\product.blend-cli.json' scene info
   ```

3. Add and edit scene data in small, verifiable steps: objects, materials,
   modifiers, cameras, lights, animation, and render settings.
4. After structural edits, inspect the relevant JSON result and save:

   ```powershell
   & $cli --json --project 'C:\work\product.blend-cli.json' scene save
   & $cli --json --project 'C:\work\product.blend-cli.json' scene info
   ```

5. For visual tasks, capture a real preview before claiming the scene looks
   correct. Inspect both returned image paths when present:

   ```powershell
   & $cli --json --project 'C:\work\product.blend-cli.json' preview capture --recipe quick
   ```

   `hero.png` is the Eevee preview for shading, materials, and framing;
   `workbench.png` is the structure-oriented view for silhouette and geometry.
   Load those local files for visual inspection rather than inferring quality
   from the scene JSON.

## Rendering and Blender availability

The `render execute` command generates `_render_script.py`; it does not itself
guarantee that Blender has executed that script. Use the returned command and
the configured Blender executable for a direct headless render, or use
`preview capture` for the harness-managed truthful preview workflow.

For operations that execute Blender, resolve the executable in this order:

1. `CLI_ANYTHING_BLENDER_PATH`
2. `BLENDER_PATH`
3. the nearest ancestor `.env.local`
4. `blender` on `PATH`

If no executable is available, complete JSON editing and script generation
when possible, then state clearly that actual rendering or preview capture was
not performed. Do not fabricate image paths or visual verification.

Use live previews for iterative visual work:

```powershell
& $cli --json --project 'C:\work\product.blend-cli.json' preview live start --recipe quick --mode poll
& $cli --json --project 'C:\work\product.blend-cli.json' preview live status --recipe quick
& $cli --json --project 'C:\work\product.blend-cli.json' preview live push --recipe quick
& $cli --json --project 'C:\work\product.blend-cli.json' preview live stop --recipe quick
```

Poll mode requires a saved project path. Preserve the returned session and
trajectory paths when the user needs reviewable visual history.

## FBX workflow

Use the dedicated `fbx` command group for FBX operations rather than creating
an ad-hoc bpy importer. Use absolute paths and verify every returned output:

```powershell
& $cli --json fbx render 'C:\assets\model.fbx' 'C:\out\model.png' --overwrite
& $cli --json fbx material-colors 'C:\assets\model.fbx' 'C:\out\materials.png' --overwrite
& $cli --json fbx multi-angle 'C:\assets\model.fbx' 'C:\out\views' --view front --view right --view top --overwrite
& $cli --json fbx smart-uv-project 'C:\assets\model.fbx' --output 'C:\out\model_uv.fbx' --overwrite
```

`smart-uv-project` validates the exported scene after Smart UV unwrapping.
Use `--overwrite-source` only when the user explicitly wants the source FBX
replaced and no separate output path is supplied.

## Completion checklist

- Confirm the CLI invocation and required Blender dependency before starting.
- Keep the project path and all artifact paths absolute.
- Use JSON output and validate command results and return codes.
- Save the project explicitly after successful edits.
- Re-query the final scene and relevant indexes.
- Capture and inspect real preview images for visual tasks.
- Report generated scripts, preview bundles, outputs, and any unavailable
  Blender-backed verification.
