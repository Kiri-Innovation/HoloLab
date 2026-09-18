# Vendored ColmapUtil build

Source: `/cloud/cloud-ssd1/Kiri4DGS/Utils/ColmapUtil` (git submodule pulls
`HoloEngineRuntime` from `github.com/Xenotech-Studio/HoloEngineRuntime`).

Used by `ColmapCamsPreview.tsx` — the canvas preview drawer for
`colmap-cams` outputs iframes `/colmaputil/index.html?embed=1` and injects
the sparse files via `postMessage`. See `docs/pack-spec.md#Previews`.

Refresh workflow (when ColmapUtil ships new features):

```
cd /cloud/cloud-ssd1/Kiri4DGS/Utils/ColmapUtil
npm ci && npm run build:embed          # base=/colmaputil/
rm -rf /cloud/cloud-ssd1/Kiri4DGS/hololab/hololab/frontend/public/colmaputil
mkdir -p /cloud/cloud-ssd1/Kiri4DGS/hololab/hololab/frontend/public/colmaputil
cp -r dist/. /cloud/cloud-ssd1/Kiri4DGS/hololab/hololab/frontend/public/colmaputil/
rm -f /cloud/cloud-ssd1/Kiri4DGS/hololab/hololab/frontend/public/colmaputil/colmaputil-send.vsix
```

The VSIX (VSCode extension) is stripped because it ships alongside the
web build in ColmapUtil's `public/` but has no purpose inside HoloLab.
