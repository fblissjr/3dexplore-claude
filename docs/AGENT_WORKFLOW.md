# Agent-assisted modeling direction

Status: planned architecture, not implemented. The current app is a local
2.5D mosaic prototype and an export foundation. The browser side deliberately
uses no UI framework; Streamlit was considered and ruled out.

## Intended outcome

Images, multiple views, or video plus a modeling brief should produce an
inspectable 3D object and a Bambu Studio project for the X2D AMS Combo. An agent
may drive external reconstruction tools, procedural modeling, mesh repair,
render inspection and iteration. A file download alone does not establish that
the requested object was modeled or that the result prints successfully.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| Browser workbench | Collect inputs, dimensions, intent and filament choices; show progress, rotatable geometry, revisions and artifacts |
| Agent harness | Choose a modeling approach, invoke tools, inspect results, revise and record evidence |
| Modeling tools | Generate geometry from images, reconstruct multiple views, or construct a procedural approximation |
| Geometry pipeline | Repair solids, preserve important detail, establish physical scale, orientation and printable features |
| AMS conversion | Convert meaningful surface colors into validated filament assignments or nonoverlapping solid parts |
| Existing exporter | Package geometry and filament intent with captured machine settings |
| Bambu Studio | Resolve machine settings, slice, expose support/purge/toolpath behavior |

Keep the harness separate from the deterministic geometry and export functions.
Do not invoke arbitrary commands supplied by a browser upload. The eventual
worker interface should expose bounded modeling operations. External service
credentials stay out of the browser and repository; external uploads and paid
runs need an explicit configured destination and budget.

## Workflow

1. Collect a brief: approximate likeness versus measured reproduction, target
   dimensions, details to preserve and loaded filament colors.
2. Inspect input quality and coverage. Extract distinct, sharp video views when
   they add evidence. A single photo cannot establish hidden surfaces or scale.
3. Choose and record the method: generative image-to-3D, multiview reconstruction,
   or procedural modeling. Label inferred and invented features.
4. Produce geometry and render multiple views, including an untextured view.
   Texture shadows must not stand in for actual folds or grooves.
5. Iterate on the shape before repairing it for print. Record meaningful changes
   and retain the previous candidate so repairs can be compared.
6. Repair, size and orient the candidate. Check closed solids, disconnected
   pieces, thickness, bed fit and any manually assigned nozzle limits.
7. Establish a successful single-color export before converting arbitrary
   textured geometry into AMS regions. Surface texture is not filament mapping.
8. Export a native project, slice it in Bambu Studio, and inspect resulting layers,
   supports, color changes, purge and print estimates. Keep slice evidence with
   the candidate. A physical print is a separate validation stage.

## First milestone: the bun (done, code retired)

A photograph of a pleated bun (a bao) became a rounded bun with geometric
pleats, rather than an extruded picture -- a procedural interpretation,
labelled as one; no reconstruction backend was selected. It met the criteria
below, and the one-object code was then removed: what it proved lives on in
`slice_local.py`, `review_layers.py` and `compare_slices.py`, which work on any
`.3mf`. The criteria still apply to the next candidate:

- Rounded volume and pleats are visible in an untextured, rotatable preview.
- A reviewer can inspect the back and underside as well as the reference view.
- The model has explicit millimeter dimensions and passes solid/placement checks.
- A single-color `.3mf` opens and slices in Bambu Studio with the captured X2D
  profile; key folds remain visible in the sliced layers.
- The report separates geometry checks, actual slice evidence and physical print
  observations. An unperformed step is never labeled successful.

After this milestone, implement color-region conversion, multiview/video input
selection, and durable job execution with revisions and resumable progress.

## Existing foundation and gaps

Implemented: local uploads, selected video frames, palette mosaics, native 3MF
packaging, captured settings reuse, direct archive preflight and regression tests.

Missing: an agent worker, reconstruction/modeling backend integration, full 3D
viewer, persistent jobs, arbitrary texture-to-AMS conversion, and automated Bambu
Studio slice validation. No backend account, paid service or remote GPU has been
configured. The exporter and preflight currently target native inline meshes,
not arbitrary nested or external-reference 3MF projects.

## Next

Not yet implemented: an unattended agent worker, a general image-to-3D
backend, a revision UI and arbitrary multicolor surface conversion. The next
harness work is a persistent job manifest and tool dispatch around the
existing commands.

Boundary: local modeling, export and slicing are in scope; print submission
and queueing are not. Physical printing stays marked not run until someone
actually prints it.
