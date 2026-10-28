---
title: Install and run
group: Quick start
order: 10
---
# Install and run

Start HoloLab on your machine, then open its interface in a browser.

## Prerequisites

The prebuilt release tarball includes the frontend and interpreter, so it does not require Node.js or a system Python installation. The requirements below apply only when installing from source.

Source installs require Python 3.10 or later (`requires-python` is `>=3.10`), Node.js 22, and Git. Algorithms use their own runtime environments. For Packs that use conda, install conda, Miniconda, or a compatible distribution. Installing HoloLab does not install the CUDA libraries, model weights, or datasets your algorithms need.

On Ubuntu 24.04, `python3 -m venv` reports `ensurepip is not available` unless the matching system venv package is installed. The Node.js 18 package supplied by `apt` also falls short of the requirement. With `sudo` access, install the system dependencies first (replace `3.12` with your Python version if it differs):

```bash
sudo apt update
sudo apt install -y git curl python3.12-venv
```

Then install Node.js 22 with nvm. Reopen the terminal when the installer finishes; the Bash example below loads nvm immediately. `node --version` should ultimately report 22.x:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install 22
node --version
```

<div class="doc-tabs doc-tabs-level-2" data-tabs data-tabs-end="prepare-pack-runtime">
<div class="doc-tab-list" role="tablist" aria-label="Installation method">
<h2 id="install-from-source"><button id="install-from-source-tab" type="button" role="tab" aria-selected="true" aria-controls="install-from-source-panel" data-tab="source">Install from source</button></h2>
<h2><button id="install-from-pip-tab" type="button" role="tab" aria-selected="false" aria-controls="install-from-pip" data-tab="pip" tabindex="-1">Install with pip</button></h2>
</div>
<section id="install-from-source-panel" role="tabpanel" aria-labelledby="install-from-source-tab" data-tab-panel="source"></section>
<section id="install-from-pip" role="tabpanel" aria-labelledby="install-from-pip-tab" data-tab-panel="pip" hidden>
<p>pip installation is being prepared for a future release. It will let you run <code>pip install hololab</code> without building the frontend locally. To start using HoloLab today, choose <strong>Install from source</strong> and complete the steps below.</p>
</section>
</div>

### Get the code

<div class="doc-tabs doc-tabs-level-3" data-tabs data-tabs-end="install-and-build-common">
<div class="doc-tab-list" role="tablist" aria-label="How to get the code">
<button id="fresh-clone" type="button" role="tab" aria-selected="true" aria-controls="get-code-clone" data-tab="clone">Fresh clone</button>
<button id="existing-checkout" type="button" role="tab" aria-selected="false" aria-controls="get-code-existing" data-tab="existing" tabindex="-1">Existing checkout</button>
</div>
<section id="get-code-clone" role="tabpanel" aria-labelledby="fresh-clone" data-tab-panel="clone"></section>
<section id="get-code-existing" role="tabpanel" aria-labelledby="existing-checkout" data-tab-panel="existing" hidden></section>
</div>

<div data-tab-source="clone"></div>

**Fresh clone.** The frontend build also builds the pinned ColmapUtil submodule, so cloning requires `--recurse-submodules`. Run these commands in a terminal:

```bash
git clone --recurse-submodules https://github.com/Kiri-Innovation/HoloLab.git
cd HoloLab
```

<div data-tab-source="existing"></div>

**Existing checkout (including Kiri4DGS).** You do not need to clone again. In the Kiri4DGS repository, HoloLab is already included as the `hololab/` submodule, so run `cd hololab` from the Kiri4DGS root. Otherwise, go to your existing HoloLab root. Then initialize and update submodules from that root:

```bash
git submodule update --init --recursive
```

### Install and build (common)

After completing either option above, run the following commands from the HoloLab root. These virtual environment paths work on Linux and macOS.

```bash
python3 -m venv .venv-runtime
.venv-runtime/bin/pip install -e ".[dev]"
cd hololab/frontend
npm ci
npm run build
cd ../..
```

## Prepare the Pack runtime

After the build, Packs can appear in the list even when their algorithm environments do not yet exist. From the HoloLab root, run:

```bash
.venv-runtime/bin/hololab pack setup --all
```

For every Pack's declared `runtime.env`, this command creates or completes the environment and installs dependencies shared by that environment. It writes any missing mappings to `~/.hololab/node/config.yaml`, after first backing up the existing configuration. When it finishes, it prints `Setup complete. Restart hololab/node so the environment mapping and cache take effect.` If HoloLab is already running, restart it so the new mappings and cached environments take effect.

## Start the services

```bash
.venv-runtime/bin/hololab start --host 127.0.0.1
```

This command starts both the Gateway and a local compute node. Leave the terminal running; press Ctrl+C to stop them.

This example binds to the loopback address. Without `--host`, the default is `0.0.0.0`, allowing access from the local network when network rules permit it. If the port is busy, add `--port 8830`.

You can check the service status from another terminal:

```bash
.venv-runtime/bin/hololab status
```

## Open the web canvas

After starting the services with the command above, open [http://127.0.0.1:8828](http://127.0.0.1:8828) in your browser. If you changed the listening address or port, use the address printed after `open` in the startup terminal. When several instances are available, the host and port in the URL determine which one you connect to.

The first screen is the workflow gallery. Click an existing workflow to open it, or choose **+ New workflow**; an empty gallery also offers **+ Create your first workflow**. Creating one opens an empty canvas at `/w/<workflow_id>` on the same host and port. You can bookmark that page to return to the workflow.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/gallery-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/gallery-light.webp" alt="Workflow gallery with only the classic STG entry retained. Names, timestamps, and other workflows are hidden. Some labels in the product UI remain in Chinese." width="656" height="280" loading="lazy" decoding="async" /></picture>
<figcaption>Workflow gallery with only the classic STG entry retained. Names, timestamps, and other workflows are hidden. Some labels in the product UI remain in Chinese.</figcaption>
</figure>

On a desktop, these are the main places to look:

- **① Canvas:** the central area holds your nodes and links—the steps and connections that make up the workflow.
- **② Packs:** the left panel lists available algorithms. Use **Search packs…** to find one and add its node to the canvas.
- **③ Execution:** **Run** in the top bar starts the workflow. Open **Jobs** in the bottom dock for **Recent jobs** and task logs; the **Runs** tab in the right dock lets you revisit run records.
- **④ Results:** expand a node's preview to inspect supported outputs. For the artifact inventory, return to the workflow gallery and open **Artifacts** (`/artifacts`).

<figure class="doc-screenshot">
<div class="annotated-canvas"><picture><source srcset="/images/docs/canvas-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/canvas-light.webp" alt="Numbers match the list above: ① canvas, ② Packs, ③ top-bar Run / right-dock Runs / bottom-dock Jobs tab, ④ node preview. The Artifacts inventory is on a separate page. Source image previews and identifiers are hidden." width="3200" height="2100" loading="lazy" decoding="async" /></picture>
<div class="canvas-annotations" aria-hidden="true"><span class="canvas-region region-canvas"><span class="region-number">1</span></span><span class="canvas-region region-packs"><span class="region-number">2</span></span><span class="canvas-region region-run"><span class="region-number">3</span></span><span class="canvas-region region-history"><span class="region-number">3</span></span><span class="canvas-region region-jobs"><span class="region-number">3</span></span><span class="canvas-region region-preview"><span class="region-number">4</span></span></div></div>
<figcaption>Numbers match the list above: ① canvas, ② Packs, ③ top-bar Run / right-dock Runs / bottom-dock Jobs tab, ④ node preview. The Artifacts inventory is on a separate page. Source image previews and identifiers are hidden.</figcaption>
</figure>

## Check the device and its Packs

Open a workflow and check that your device is online in **Compute Nodes · Pulse** in the bottom dock. Its settings gear is at the upper right of the device card. The **Packs** panel lists algorithms found in the compute node's manifests. A listed Pack does not mean its algorithm environment is installed.

<figure class="doc-screenshot">
<picture><source srcset="/images/docs/panels-dark.webp" media="(prefers-color-scheme: dark)" type="image/webp" /><img src="/images/docs/panels-light.webp" alt="Paired crops of the left Packs panel and the bottom Compute Nodes · Pulse tab: algorithm access and device status. Device names, IDs, and paths are hidden." width="1536" height="724" loading="lazy" decoding="async" /></picture>
<figcaption>Paired crops of the left Packs panel and the bottom Compute Nodes · Pulse tab: algorithm access and device status. Device names, IDs, and paths are hidden.</figcaption>
</figure>

Prepare the GPU and input data required by the Pack you plan to run.

Once you know your way around and the device is ready, follow [Your first workflow](workflows.md) to connect and run your first steps. If the interface will not open or the device stays offline, see [Troubleshooting](troubleshooting.md).
