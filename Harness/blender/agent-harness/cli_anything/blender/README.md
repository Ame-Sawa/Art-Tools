# Blender CLI - Agent Harness

A stateful command-line interface for 3D scene editing, following the same
patterns as the GIMP CLI harness. Uses a JSON scene description format
with bpy script generation for actual Blender rendering.

## Installation

```bash
# From the agent-harness directory:
pip install click prompt_toolkit

# No Blender installation required for scene editing.
# Blender is only needed if you want to execute the generated render scripts.
```

## Configure Blender for Rendering

The CLI can edit scene JSON without Blender. To render or capture previews, it
must locate the Blender executable. The portable, no-administrator setup is a
machine-local `.env.local` file:

1. Copy `../../.env.example` to `.env.local` in the `agent-harness` directory,
   or create `.env.local` in any parent workspace directory.
2. Set the absolute executable path:

   ```text
   CLI_ANYTHING_BLENDER_PATH=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe
   ```

   On macOS, use `/Applications/Blender.app/Contents/MacOS/Blender`.

The resolver checks, in order: `CLI_ANYTHING_BLENDER_PATH`, `BLENDER_PATH`,
the first ancestor `.env.local`, and then `blender` on `PATH`. The `.env.local`
option is recommended when moving the harness between machines because the
machine-specific path remains untracked. Verify the setup with:

```powershell
$harness = "<absolute path to agent-harness>"
python -c "import sys; sys.path.insert(0, r'$harness'); from cli_anything.blender.utils.blender_backend import find_blender, get_version; print(find_blender()); print(get_version())"
```

## Quick Start

```bash
# Create a new scene
python3 -m cli.blender_cli scene new --name "MyScene" -o scene.json

# Add objects
python3 -m cli.blender_cli --project scene.json object add cube --name "Box"
python3 -m cli.blender_cli --project scene.json object add sphere --name "Ball" -l 3,0,1

# Create and assign materials
python3 -m cli.blender_cli --project scene.json material create --name "Red" --color 1,0,0,1
python3 -m cli.blender_cli --project scene.json material assign 0 0

# Add modifiers
python3 -m cli.blender_cli --project scene.json modifier add subdivision_surface -o 0 -p levels=2

# Add camera and light
python3 -m cli.blender_cli --project scene.json camera add -l 7,-6,5 -r 63,0,46 --active
python3 -m cli.blender_cli --project scene.json light add sun -r -45,0,30

# Save
python3 -m cli.blender_cli --project scene.json scene save

# Generate render script
python3 -m cli.blender_cli --project scene.json render execute render.png --overwrite

# Execute with Blender (if installed)
blender --background --python /path/to/_render_script.py
```

## JSON Output Mode

All commands support `--json` for machine-readable output:

```bash
python3 -m cli.blender_cli --json scene new -o scene.json
python3 -m cli.blender_cli --json --project scene.json object list
```

## Preview and Live Preview

Blender exposes real preview bundles through the `preview` command group.

```bash
# Capture a real preview bundle
cli-anything-blender --json --project scene.blend-cli.json preview capture --recipe quick

# Query the latest bundle
cli-anything-blender --json --project scene.blend-cli.json preview latest --recipe quick

# Start a poll-mode live preview session
cli-anything-blender --json --project scene.blend-cli.json preview live start --recipe quick --mode poll --source-poll-ms 500

# Query current live-session state
cli-anything-blender --json --project scene.blend-cli.json preview live status --recipe quick
```

The default `quick` recipe produces real Blender-rendered `hero.png` and
`workbench.png` artifacts. Live sessions persist:

- `session.json`
- immutable bundle directories
- `trajectory.json`

Inspect or open published preview state with:

```bash
cli-hub previews inspect /path/to/bundle-or-session
cli-hub previews html /path/to/bundle-or-session -o page.html
cli-hub previews watch /path/to/session --open
cli-hub previews open /path/to/bundle-or-session
```

## Interactive REPL

```bash
python3 -m cli.blender_cli repl
# or with existing project:
python3 -m cli.blender_cli repl --project scene.json
```

## Command Groups

### Scene Management
```
scene new      - Create a new scene
scene open     - Open an existing scene file
scene save     - Save the current scene
scene info     - Show scene information
scene profiles - List available scene profiles
scene json     - Print raw scene JSON
```

### Object Management
```
object add       - Add a primitive (cube, sphere, cylinder, cone, plane, torus, monkey, empty)
object remove    - Remove an object by index
object duplicate - Duplicate an object
object transform - Translate, rotate, or scale an object
object set       - Set an object property
object list      - List all objects
object get       - Get detailed object info
```

### Material Management
```
material create - Create a new Principled BSDF material
material assign - Assign a material to an object
material set    - Set a material property
material list   - List all materials
material get    - Get detailed material info
```

### Modifier Management
```
modifier list-available - List all available modifier types
modifier info           - Show modifier details
modifier add            - Add a modifier to an object
modifier remove         - Remove a modifier
modifier set            - Set a modifier parameter
modifier list           - List modifiers on an object
```

### Camera Management
```
camera add        - Add a camera
camera set        - Set a camera property
camera set-active - Set the active camera
camera list       - List all cameras
```

### Light Management
```
light add  - Add a light (point, sun, spot, area)
light set  - Set a light property
light list - List all lights
```

### Animation
```
animation keyframe        - Set a keyframe on an object
animation remove-keyframe - Remove a keyframe
animation frame-range     - Set the animation frame range
animation fps             - Set the FPS
animation list-keyframes  - List keyframes for an object
```

### Render
```
render settings - Configure render settings
render info     - Show current render settings
render presets  - List available render presets
render execute  - Render the scene (generates bpy script)
render script   - Generate bpy script to stdout
```

### FBX Import and Render
```
fbx render <input.fbx> <output.png>              - Import FBX into an empty scene and render it
fbx material-colors <input.fbx> <output.png>     - Render with a distinct color for each mesh material slot
fbx multi-angle <input.fbx> <output-dir>         - Render several views in one Blender run
fbx smart-uv-project <input.fbx>                 - Smart UV unwrap all meshes and export an FBX
fbx auto-uv <input.fbx>...                       - Select a UV algorithm and export one or more validated FBX files
```

These FBX commands execute Blender headlessly, set up an automatic camera and
three-point area lighting based on the imported model bounds, then verify that
the output file was written. Typical use:

```powershell
cli-anything-blender fbx render .\model.fbx .\output\model.png --overwrite
cli-anything-blender fbx material-colors .\model.fbx .\output\parts.png --overwrite
cli-anything-blender fbx multi-angle .\model.fbx .\output\views --overwrite
cli-anything-blender fbx multi-angle .\model.fbx .\output\views --view front --view right --view top --material-colors --overwrite
cli-anything-blender fbx smart-uv-project .\model.fbx
cli-anything-blender fbx smart-uv-project .\model.fbx --output .\output\model_uv.fbx --overwrite
cli-anything-blender fbx smart-uv-project .\model.fbx --overwrite-source
cli-anything-blender fbx auto-uv .\model.fbx
cli-anything-blender fbx auto-uv .\model.fbx --algorithm autouv --suffix _autouv --unwrap-exe .\cli_anything\blender\third_party\MinistryOfFlat\UnWrapConsole3.exe
cli-anything-blender fbx auto-uv .\model.fbx --algorithm autouv --output .\output\model_autouv.fbx --resolution 2048 --udims 2 --separate-hard-edges
cli-anything-blender fbx auto-uv .\model.fbx --no-merge-meshes --no-normalize-uv --timeout 600 --external-timeout 180
cli-anything-blender fbx auto-uv .\model.fbx --algorithm uniform
cli-anything-blender fbx auto-uv .\model.fbx --algorithm uniform --suffix _uv
cli-anything-blender fbx auto-uv .\model.fbx --algorithm uniform --output .\output\model_uniform_uv.fbx --overwrite
cli-anything-blender fbx auto-uv .\model.fbx --algorithm uniform --angle-deg 15 --angle-deg 20 --angle-deg 30
cli-anything-blender fbx auto-uv .\model.fbx --algorithm uniform --rotate-method AXIS_ALIGNED_X
cli-anything-blender fbx auto-uv .\a.fbx .\b.fbx --algorithm autouv --suffix _autouv
cli-anything-blender fbx auto-uv .\a.fbx .\b.fbx --algorithm uniform --output-dir .\output --suffix _uv
cli-anything-blender --json fbx auto-uv .\a.fbx .\b.fbx --algorithm autouv --topology-prefilter-level medium
cli-anything-blender --json fbx auto-uv .\a.fbx .\b.fbx --algorithm autouv --jobs 2
```

`fbx smart-uv-project` writes `model_uv.fbx` beside the source by default. It
imports the complete FBX scene, unwraps each unique mesh datablock with
Blender's Smart UV Project, and exports the complete scene after validating
object names, hierarchy, transforms, handedness, mesh structure, animation,
and UV presence. `--overwrite-source` is required to replace the input file;
`--overwrite` permits replacing an existing output path. Smart UV parameters
such as `--angle-limit`, `--margin-method`, `--rotate-method`,
`--island-margin`, `--area-weight`, `--correct-aspect`, and
`--scale-to-bounds` are available as command options.

`fbx auto-uv --algorithm uniform` ignores the source UV coordinates, removes existing UV
layers, and searches Smart UV angle candidates. It selects the result by
minimizing area-weighted P95 local checker stretch, then maximum stretch, then
texel-density variation. UV island count, packing efficiency, and texture
continuity are not part of this objective. Default angle candidates are 10,
15, 20, 25, 30, 40, 50, 60, and 66 degrees; repeat `--angle-deg` to provide a
custom candidate set. Use `--rotate-method AXIS_ALIGNED_X` for horizontal island
alignment, `--rotate-method AXIS_ALIGNED_Y` for vertical alignment, or
`--rotate-method AXIS_ALIGNED` to let Blender choose the minimal rectangle
orientation. The command emits every candidate's metrics and the selected
angle in JSON mode, then performs the same FBX round-trip validation as
`smart-uv-project`. By default it replaces the input FBX only after the export
and validation succeed. Use `--suffix _uv` to write a sibling file such as
`model_uv.fbx`, or `--output <path>` to write to an additional path. Existing
non-source outputs require `--overwrite`; `--overwrite-source` remains accepted
as an explicit compatibility flag for the in-place mode.

`fbx auto-uv --algorithm autouv` uses the Ministry of Flat `UnWrapConsole3.exe` bundled inside the
Harness package at `cli_anything/blender/third_party/MinistryOfFlat/`. By default, when `UDIMS=1`,
all unique Mesh datablocks in each FBX are converted to one temporary world-space OBJ and the
external program is called once. The result is strictly checked for unchanged vertices, face order,
corner counts and topology before its UVs are mapped back to the original active UV Maps. Use
`--no-merge-meshes` for independent per-Mesh calls. `--merge-meshes` never merges different input
FBX files. Other UV Maps and scene structure are preserved.

When `UDIMS=1`, UV normalization is enabled by default and can be disabled with
`--no-normalize-uv`. Merged mode applies one uniform transform to all Meshes; non-merged mode
normalizes each Mesh independently. The transform uses the same margin `1 / resolution` and does
not rearrange islands. `UDIMS>1` always skips both merging and normalization to preserve UDIM
coordinates. World-scale UV remains compatible with normalization, but normalization can change
absolute texel density and the result reports a warning. Blender `Pack Islands` is not used.

For standalone installations, `--unwrap-exe` or `MINISTRY_OF_FLAT_EXE` can override the bundled
executable. The per-file Blender timeout defaults to 300 seconds and the external process timeout
defaults to 120 seconds; both are configurable without a fixed 10-second cap.
The default external settings are resolution 1024, one UDIM, square pixels,
and all overlap, world-scale, normals, and hard-edge options disabled. Common
settings can be changed with `--resolution`, `--separate-hard-edges`, `--aspect`,
`--use-normals`, `--udims`, `--overlap-identical`, `--overlap-mirrored`,
`--world-scale`, and `--density`.

AutoUV performs a versioned topology-risk preflight (currently `risk_version: 2`)
by default after importing the FBX and before exporting temporary OBJ files or
starting `UnWrapConsole3.exe`. The fixed score checks mesh size, boundary edge
ratio/count, n-gon density, large faces, duplicate positions, zero-area faces,
interior non-manifold edges, and the boundary-plus-n-gon combinations that are
known to trigger slow paths. Each metric uses only its highest scoring band.
The result includes the measured values and `triggered_rules`; a high-risk FBX
is reported as `skipped`, produces no output, and does not start the external tool.
The filter level is selected with `--topology-prefilter-level`:
`high` is the standard default and skips only high-risk files, `medium` is a
strict mode that skips medium- and high-risk files, and `off` keeps the
diagnostic but never blocks on topology risk. The legacy
`--topology-prefilter` and `--no-topology-prefilter` flags remain accepted as
aliases for `high` and `off`; do not combine them with the new level option.
Uniform UV never uses this preflight.

For batch jobs, progress is reported once per file. In `--json` mode progress
events are JSON Lines on stderr, while the final aggregate JSON remains the only
document written to stdout. Processing continues after individual failures or
topology skips; the final exit code is non-zero if either occurred, and the
summary contains `success_count`, `failure_count`, and `skipped_count`.

AutoUV always sends the original imported mesh topology to Ministry of Flat.
The Harness does not triangulate or clean a derived temporary mesh; it validates
the returned OBJ topology and copies its UVs directly to the active UV map.
Each AutoUV input FBX uses a configurable processing budget (300 seconds by
default) covering Blender import, temporary mesh work, the external call, FBX
export, and round-trip validation. If the budget is exceeded, that FBX is
skipped without committing an output and the batch continues. The external
process is launched with a controllable `Popen` lifetime; on timeout the Windows
process tree is terminated with `taskkill /T /F` (or the Unix process group is
terminated), and the result records whether cleanup succeeded.

Batch processing uses up to two independent Blender/Ministry of Flat workers by
default. Use `--jobs 1` for serial processing or increase `--jobs` when the
machine has sufficient CPU and memory. Files are scheduled independently, the
final JSON results remain in input order, and progress events include the
completed-file count. A failed or timed-out worker releases its slot so later
files continue processing. Use the GUI's internal `--cancel-file` mechanism or
send `Ctrl+C`/`SIGTERM` to request cooperative cancellation. The CLI stops
submitting new files, terminates each registered Blender process tree, removes
uncommitted temporary outputs, and returns `cancelled_count` with exit code
`130`. Successfully committed files remain intact; a forced process-tree kill
is only used as a last resort.

Both algorithms support multiple FBX inputs in one invocation. Without an
output option each source is replaced after successful validation. Use
`--suffix` for sibling outputs or `--output-dir` for a shared output folder;
`--output` remains limited to a single input. Batch processing continues after
individual failures and returns a per-file JSON result with a non-zero exit
code when any file fails. Progress is reported once per file: in `--json` mode,
single-line progress JSON events are written to stderr while the final batch
summary remains valid JSON on stdout. Without `--json`, stderr shows human-readable
`[index/total]` messages. A failed file includes its error text in both the
progress event and the final per-file result.

Supported output extensions are `.png`, `.jpg`, `.jpeg`, `.bmp`, `.tif`,
`.tiff`, and `.exr`. Use `--engine CYCLES`, `--resolution-x`,
`--resolution-y`, `--samples`, or `--transparent` to tune the render.
`multi-angle` defaults to `front`, `right`, `top`, and the elevated
`perspective` view; use repeated `--view` options to request other directions
such as `back`, `left`, or `bottom`.

FBX renders use a structure-first default lighting setup: low world illumination,
a directional key light, restrained fill light, and rim light, with AgX medium-high
contrast where supported by the Blender version. This keeps edges and surface
relief legible instead of washing the model out.

Meshes without a material, or materials whose texture files cannot be found, use
a neutral dark-gray fallback instead of Blender's magenta missing-texture color.

### Session
```
session status  - Show session status
session undo    - Undo the last operation
session redo    - Redo the last undone operation
session history - Show undo history
```

## Running Tests

```bash
# From the agent-harness directory:

# Run all tests
python3 -m pytest cli/tests/ -v

# Run unit tests only
python3 -m pytest cli/tests/test_core.py -v

# Run E2E tests only
python3 -m pytest cli/tests/test_full_e2e.py -v

# Run with coverage
python3 -m pytest cli/tests/ -v --tb=short
```

## Architecture

```
cli/
├── __init__.py
├── __main__.py           # python3 -m cli.blender_cli
├── blender_cli.py        # Main CLI entry point (Click + REPL)
├── core/
│   ├── __init__.py
│   ├── scene.py          # Scene create/open/save/info
│   ├── objects.py        # 3D object management
│   ├── materials.py      # Material management
│   ├── modifiers.py      # Modifier registry + add/remove/set
│   ├── lighting.py       # Camera and light management
│   ├── animation.py      # Keyframe and timeline management
│   ├── render.py         # Render settings and export
│   └── session.py        # Stateful session, undo/redo
├── utils/
│   ├── __init__.py
│   └── bpy_gen.py        # Blender Python script generation
└── tests/
    ├── __init__.py
    ├── test_core.py      # Unit tests (synthetic data, 100+ tests)
    └── test_full_e2e.py  # E2E tests (script gen, roundtrips, workflows)
```

## JSON Scene Format

The scene is stored as a JSON file with this structure:

```json
{
  "version": "1.0",
  "name": "scene_name",
  "scene": { "fps": 24, "frame_start": 1, "frame_end": 250, ... },
  "render": { "engine": "CYCLES", "resolution_x": 1920, "samples": 128, ... },
  "world": { "background_color": [0.05, 0.05, 0.05], ... },
  "objects": [ { "name": "Cube", "mesh_type": "cube", "location": [0,0,0], ... } ],
  "materials": [ { "name": "Material", "color": [0.8,0.8,0.8,1], ... } ],
  "cameras": [ { "name": "Camera", "focal_length": 50, ... } ],
  "lights": [ { "name": "Light", "type": "POINT", "power": 1000, ... } ],
  "collections": [ { "name": "Collection", "objects": [0, 1] } ],
  "metadata": { "created": "...", "modified": "...", "software": "blender-cli 1.0" }
}
```

## Rendering

Since Blender's `.blend` format is binary, this CLI uses a JSON scene format
and generates Blender Python (bpy) scripts for rendering. The workflow:

1. Edit the scene using CLI commands (creates/modifies JSON)
2. Generate a bpy script with `render execute` or `render script`
3. Run the script with `blender --background --python script.py`

The generated scripts reconstruct the entire scene in Blender and render it.
