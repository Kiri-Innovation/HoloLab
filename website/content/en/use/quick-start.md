---
title: Install and run
group: Quick start
order: 10
---
# Install and run

Start HoloLab on your machine, then open its interface in a browser.

## Prerequisites

Installing from source requires Python 3.10 or later, Node.js 22, and Git. Algorithms use their own runtime environments. For Packs that use conda, install conda, Miniconda, or a compatible distribution. Installing HoloLab does not install the CUDA libraries, model weights, or datasets your algorithms need.

## Install from source

Run these commands in a terminal:

```bash
git clone --recurse-submodules https://github.com/Kiri-Innovation/HoloLab.git
cd HoloLab
python3 -m venv .venv-runtime
.venv-runtime/bin/pip install -e ".[dev]"
cd hololab/frontend
npm ci
npm run build
cd ../..
```

These virtual environment paths work on Linux and macOS. The frontend build also builds the pinned ColmapUtil submodule, so clone with `--recurse-submodules`. For an existing checkout, run `git submodule update --init --recursive` first.

## Start HoloLab

```bash
.venv-runtime/bin/hololab start --host 127.0.0.1
```

Open [http://127.0.0.1:8828](http://127.0.0.1:8828). This command starts both the Gateway and a local compute node. Leave the terminal running; press Ctrl+C to stop them.

This example binds to the loopback address. Without `--host`, the default is `0.0.0.0`, allowing access from the local network when network rules permit it. If the port is busy, add `--port 8830` and use that port in your browser.

You can check the service status from another terminal:

```bash
.venv-runtime/bin/hololab status
```

## Open the web canvas

Use the address printed after `open` in the startup terminal. With the command above, it is `http://127.0.0.1:8828`; if you chose `--port 8830`, open `http://127.0.0.1:8830` instead. When several instances are available, the host and port in the URL determine which one you connect to.

The first screen is the workflow gallery. Click an existing workflow to open it, or choose **+ New workflow**; an empty gallery also offers **+ Create your first workflow**. Creating one opens an empty canvas at `/w/<workflow_id>` on the same host and port. You can bookmark that page to return to the workflow.

On a desktop, these are the main places to look:

- **Canvas:** the central area holds your nodes and links—the steps and connections that make up the workflow.
- **Packs:** the left panel lists available algorithms. Use **Search packs…** to find one and add its node to the canvas.
- **Execution:** **Run** in the top bar starts the workflow. **Recent jobs** shows task status and provides logs; **Runs** in the right sidebar lets you revisit run records.
- **Results:** expand a node's preview to inspect supported outputs. For the artifact inventory, return to the workflow gallery and open **Artifacts** (`/artifacts`).

## Check the device and its Packs

Open a workflow and check that your device is online in **Compute nodes**. The **Packs** panel lists algorithms found in the compute node's manifests. A listed Pack does not mean its algorithm environment is installed.

The compute node configuration defaults to `~/.hololab/node/config.yaml`. If a Pack declares an environment named `research`, map that name to the environment's actual path on the device:

```yaml
conda_bin: /path/to/conda
envs:
  research: /path/to/conda/envs/research
```

Replace the example paths with your own. Prepare the GPU and input data required by the Pack you plan to run.

Once you know your way around and the device is ready, follow [Your first workflow](workflows.md) to connect and run your first steps. If the interface will not open or the device stays offline, see [Troubleshooting](troubleshooting.md).
