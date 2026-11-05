"""Public port spelling and template bindings, without changing geometry contracts."""
from pathlib import Path
import unittest
from hololab.manifest import load_manifest
from hololab.manifest.render import RenderContext, render_manifest

ROOT=Path(__file__).resolve().parents[1]
class Ports(unittest.TestCase):
    def test_public_ports_and_render(self):
        paths=[ROOT.parent/'tryouts/mega-sam/packs/megasam-track@0.7.0',
               ROOT/'community_packs/merge-colmap4d@0.4.0',
               ROOT/'community_packs/frames-to-colmap4d@0.1.0',
               ROOT/'community_packs/colmap4d-split@0.1.0']
        for path in paths:
            with self.subTest(pack=path.name):
                m,_=load_manifest(path/'manifest.yaml')
                ports=m.inputs if 'split' in path.name else m.outputs
                self.assertEqual(set(ports),{'colmap4d'})
                self.assertEqual(ports['colmap4d'].tags,['colmap4d'])
                result=render_manifest(m,RenderContext(
                    inputs={k:'/input/'+k for k in m.inputs},
                    outputs={k:'/output/'+k for k in m.outputs},
                    params={'strict_validate':False},pack_dir=str(path),
                    package_root=str(path.parents[1]),scratch_dir='/scratch'))
                self.assertIn('/input/colmap4d' if 'split' in path.name else '/output/colmap4d',result.shell)

    def test_released_merge_preserved(self):
        old=ROOT/'community_packs/merge-colmap4d@0.3.0'
        new=old.with_name('merge-colmap4d@0.4.0')
        m,_=load_manifest(old/'manifest.yaml');self.assertEqual(set(m.outputs),{'model'})
        for name in ('merge_colmap4d.py','strict_legacy.py'):
            self.assertEqual((old/name).read_bytes(),(new/name).read_bytes())

if __name__=='__main__':unittest.main()
