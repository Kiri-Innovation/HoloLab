from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image
import pillow_heif

name = '305186217781234'
source = Path('/cloud/cloud-ssd1/Kiri4DGS/data/persona_bench/beach_sunglasses_hat_sitting/input/image')
old = pillow_heif.open_heif(source / (name + '.heic'), convert_hdr_to_8bit=False).to_pillow()
new = Image.open('/tmp/hololab-heic-rotation/frames/' + name + '.png')
fig, axes = plt.subplots(1, 2, figsize=(10, 7))
for ax, im, title in zip(axes, [old, new], ['Before: raw decoded pixels (1920 x 1440)', 'After: orientation baked in (1440 x 1920)']):
    ax.imshow(im)
    ax.set_title(title, fontsize=11)
    ax.axis('off')
fig.tight_layout()
fig.savefig('reports/heic-rotation-20261103/comparison.png', dpi=130)
