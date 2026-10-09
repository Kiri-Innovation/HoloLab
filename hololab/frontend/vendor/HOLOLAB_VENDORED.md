# ColmapUtil source and build

ColmapUtil is an MIT-licensed public submodule at `vendor/ColmapUtil`, using
https://github.com/Kiri-Innovation/ColmapUtil.git. HoloLab pins commit
`20c9a13d7842621b1887b64ccf7a4b577779f69c`; its recursive HoloEngineRuntime
submodule pins `093e6c0b8ea6329959746e5b5dd16cc25609fe42`.
Generated `public/colmaputil/` and vendor build caches are ignored, not committed.
The iframe URL and postMessage protocol remain `/colmaputil/index.html?embed=1`
and `colmap-ready` / `colmap-load-files`.

## Setup: server, Mac and XENO-PC

Use Git, Node.js 22 and npm on each machine. The same commands work in bash,
PowerShell and cmd (run each line separately):

```text
# From the HoloLab repository root, including after pulling updates:
git submodule update --init --recursive
cd hololab/frontend
npm ci
npm run build
# For development instead:
npm run dev
```

New clones can use `git clone --recurse-submodules <HoloLab URL>`. No GitHub
credentials are needed for either vendor repository. Do not copy node_modules
between Linux, macOS and Windows: npm installs platform-specific packages.
Neither the build nor these setup commands restart HoloLab services.

## Build and dev behavior

- `npm run build:vendor` checks recursive checkout availability, installs vendor
  dependencies with `npm ci` if needed, runs `npm run build:embed`, validates
  local HTML assets and publishes generated files to `public/colmaputil/`.
- `npm run build` always runs this vendor build before TypeScript and Vite.
- `npm run dev` runs a predev check. It builds only when source, dependency
  identity, build script or generated output changed/missing. Source fingerprints
  include nested runtime commits, dirty tracked files and non-ignored new files.
  Vendor edits during a running dev server require `npm run build:vendor` or a
  dev restart; vendor source is not watched automatically.
- Installation records live inside vendor node_modules. They include both
  package files, Node/npm identity, OS and architecture. Deleting node_modules,
  changing the lockfile or changing environments triggers installation. Matching
  dependencies are reused; production builds still rebuild vendor assets.
- `npm ci` needs the registry on first install. Git initialization needs GitHub.
  Prepared source and dependencies allow subsequent builds without downloads.
  A partial npm cache alone does not guarantee offline installation.

The script uses Node filesystem/process APIs, not shell cp/rm commands. It
builds into a staging directory on the same filesystem before publication.
Each directory rename is atomic; replacing an existing nonempty directory
requires moving it to a backup first, then renaming the stage into place.
There is a brief gap between these two renames (portable Node does not offer
atomic directory exchange). A failed publish restores the previous directory
and exits nonzero. Failed builds never continue into HoloLab's build/dev command.
No partially copied directory is served. A lock prevents concurrent vendor
builds from racing. After an interrupted process, remove `vendor/.cache/build.lock`
only after verifying no build is running; the next run restores a pending backup.

The upstream tracked `public/colmaputil-send.vsix` is untouched. The copied VSIX
is removed from vendor dist, excluded from staging, and must not appear in
HoloLab's generated public/dist output. The upstream extension is not built.

## Troubleshooting

Missing submodule: run `git submodule update --init --recursive` from the root.
Missing dependencies: build:vendor attempts npm ci and reports the actual
installation failure. Fix network/registry access and retry `npm run build:vendor`
from `hololab/frontend`. Errors include:
“缺少 vendor 构建，请先运行 `npm run build:vendor`”.

To force dependency reinstallation, remove
`vendor/ColmapUtil/node_modules/.hololab-deps.json`, then run `npm run build:vendor`.
A lockfile change triggers this automatically. For clean-build verification,
use a fresh recursive clone, `npm ci`, then `npm run build`.

## Updating the pinned source

Publish the runtime commit first, then the ColmapUtil commit referencing it.
Check out the desired *published* ColmapUtil commit in the submodule, update
its recursive submodules, and run `npm run build`. Validate iframe assets,
`colmap-ready` and camera/point-cloud rendering before committing only the
HoloLab gitlink and any relevant build/documentation changes. Never commit
generated public/dist files. This replaces the former manual dist-copy workflow.

## Browser smoke test

After `npm run build`, serve the result on a free port:
`npm run preview -- --host 127.0.0.1 --port 15173 --strictPort`.
In another terminal, run
`node scripts/smoke-colmap.mjs http://127.0.0.1:15173`. Install the Playwright
Chromium browser first if needed (`npx playwright install chromium`).
The test opens a same-origin fixture containing the production iframe, waits
for its handshake, sends three camera poses and 125 colored points, verifies
WebGL draw calls and no failed resources/page errors, and saves a screenshot
and JSON evidence in `test-results/colmap-vendor/`. This checks the embedded
viewer without depending on a running gateway or altering production ports.
