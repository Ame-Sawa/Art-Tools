# cli-anything-blender command reference

Use this reference for exact syntax. Confirm details with `--help` if the
repository CLI changes. Global options must appear before the command group.

## Invocation

```text
cli-anything-blender [--json] [--project ABSOLUTE_PROJECT] [--dry-run] COMMAND
python -m cli_anything.blender [--json] [--project ABSOLUTE_PROJECT] [--dry-run] COMMAND
```

Without a command, the CLI enters the interactive REPL. Prefer one-shot
commands for agent automation.

## Scene

```text
scene new [--name NAME] [--profile PROFILE] [--resolution-x PX] [--resolution-y PX]
          [--engine CYCLES|EEVEE|WORKBENCH] [--samples N] [--fps N]
          [--output ABSOLUTE_PROJECT]
scene open ABSOLUTE_PROJECT
scene save [ABSOLUTE_PROJECT]
scene info
scene profiles
scene json
```

Use `scene new --output` to create a saved project, then pass that same path
with the global `--project` option for subsequent commands.

## Objects

```text
object add cube|sphere|cylinder|cone|plane|torus|monkey|empty
             [--name NAME] [--location X,Y,Z] [--rotation X,Y,Z]
             [--scale X,Y,Z] [--param KEY=VALUE]... [--collection NAME]
object remove INDEX
object duplicate INDEX
object transform INDEX [--translate DX,DY,DZ] [--rotate RX,RY,RZ] [--scale SX,SY,SZ]
object set INDEX PROP VALUE
object list
object get INDEX
```

Object properties include `name`, `visible`, `location`, `rotation`, `scale`,
and `parent`. Rotations and transform vectors use comma-separated values;
rotations are degrees at the CLI boundary.

## Materials

```text
material create [--name NAME] [--color R,G,B,A] [--metallic N]
                [--roughness N] [--specular N]
material assign MATERIAL_INDEX OBJECT_INDEX
material set INDEX PROP VALUE
material list
material get INDEX
```

Material properties include color, emission color, metallic, roughness,
specular, alpha, and backface culling. Color values use normalized floats.

## Modifiers

```text
modifier list-available [--category generate|deform]
modifier info NAME
modifier add TYPE [--object OBJECT_INDEX] [--name NAME] [--param KEY=VALUE]...
modifier remove MODIFIER_INDEX [--object OBJECT_INDEX]
modifier set MODIFIER_INDEX PARAM VALUE [--object OBJECT_INDEX]
modifier list [--object OBJECT_INDEX]
```

The built-in registry includes subdivision surface, mirror, array, bevel,
solidify, decimate, boolean, and smooth. Query `list-available` and `info`
before relying on a parameter name.

## Cameras and lights

```text
camera add [--name NAME] [--location X,Y,Z] [--rotation X,Y,Z]
           [--type PERSP|ORTHO|PANO] [--focal-length MM] [--active]
camera set INDEX PROP VALUE
camera set-active INDEX
camera list

light add point|sun|spot|area [--name NAME] [--location X,Y,Z]
          [--rotation X,Y,Z] [--energy N] [--color R,G,B]
light set INDEX PROP VALUE
light list
```

Use `camera list` and `light list` to discover indexes before setting or
activating entries. Check command help for the complete light-specific option
surface when changing spot, area, or sun parameters.

## Animation

```text
animation keyframe OBJECT_INDEX FRAME PROP VALUE [--interpolation TYPE]
animation remove-keyframe OBJECT_INDEX FRAME [--prop PROP]
animation frame-range START END
animation fps FPS
animation list-keyframes OBJECT_INDEX [--prop PROP]
```

For `location`, `rotation`, and `scale`, pass a comma-separated vector as the
value. Set the frame range and FPS before authoring a longer animation.

## Render

```text
render settings [--engine CYCLES|EEVEE|WORKBENCH] [--resolution-x PX]
                [--resolution-y PX] [--resolution-percentage N] [--samples N]
                [--denoising|--no-denoising] [--transparent|--no-transparent]
                [--format FORMAT] [--output-path ABSOLUTE_PATH]
                [--preset PRESET]
render info
render presets
render execute ABSOLUTE_OUTPUT [--frame N] [--animation] [--overwrite]
render script ABSOLUTE_OUTPUT [--frame N] [--animation]
```

`render execute` writes a generated bpy script and returns its path. Use
`preview capture` or explicitly run the generated script through a configured
Blender executable when an image is required.

## Preview and live preview

```text
preview recipes
preview capture [--recipe NAME] [--force] [--root-dir ABSOLUTE_DIR]
preview latest [--recipe NAME] [--root-dir ABSOLUTE_DIR]
preview live start [--recipe NAME] [--force] [--root-dir ABSOLUTE_DIR]
                   [--poll-ms N] [--mode poll|manual] [--source-poll-ms N] [--open]
preview live push [--recipe NAME] [--force] [--root-dir ABSOLUTE_DIR] [--poll-ms N]
preview live status [--recipe NAME] [--root-dir ABSOLUTE_DIR]
preview live stop [--recipe NAME] [--root-dir ABSOLUTE_DIR]
```

The `quick` recipe returns a manifest with real `hero.png` and `workbench.png`
artifacts. Poll mode watches a saved project and publishes updated bundles;
manual mode requires explicit `live push` commands.

## FBX

```text
fbx render ABSOLUTE_INPUT_FBX ABSOLUTE_OUTPUT_IMAGE
    [--engine CYCLES|EEVEE|WORKBENCH] [--resolution-x PX] [--resolution-y PX]
    [--samples N] [--transparent] [--overwrite] [--timeout SECONDS]

fbx material-colors ABSOLUTE_INPUT_FBX ABSOLUTE_OUTPUT_IMAGE
    [same render options]

fbx multi-angle ABSOLUTE_INPUT_FBX ABSOLUTE_OUTPUT_DIR
    [--view front|back|left|right|top|bottom|perspective]...
    [--format png|jpg|jpeg|bmp|tif|tiff|exr] [--material-colors]
    [same render options]

fbx smart-uv-project ABSOLUTE_INPUT_FBX
    [--output ABSOLUTE_OUTPUT_FBX | --overwrite-source] [--overwrite]
    [--timeout SECONDS] [--angle-limit RADIANS]
    [--margin-method METHOD] [--rotate-method METHOD] [--island-margin N]
    [--area-weight N] [--correct-aspect|--no-correct-aspect]
    [--scale-to-bounds|--no-scale-to-bounds]

fbx auto-uniform-uv ABSOLUTE_INPUT_FBX
    [--output ABSOLUTE_OUTPUT_FBX | --overwrite-source] [--overwrite]
    [--timeout SECONDS] [--angle-deg DEGREES]...
```

FBX render and Smart UV commands execute Blender headlessly and verify their
outputs. Keep source files intact by choosing a separate output path unless
replacement is explicitly requested.

`auto-uniform-uv` removes source UV layers and re-unwraps each unique mesh. It
searches the default angle candidates 10, 15, 20, 25, 30, 40, 50, 60, and 66
degrees unless repeated `--angle-deg` values are supplied. It selects by
area-weighted P95 local stretch, maximum stretch, then texel-density variation;
UV island count and packing efficiency are not optimization targets. JSON mode
reports every candidate and the selected metrics before the normal FBX
round-trip validation.

## Session

```text
session status
session undo
session redo
session history
```

Session undo/redo is process-local. Save the project explicitly when the
result must persist across separate CLI invocations.
