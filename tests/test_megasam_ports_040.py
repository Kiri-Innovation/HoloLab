"""Port contracts and image-array timing; no GPU."""
import json
from pathlib import Path
import tempfile
import unittest
import yaml
from PIL import Image, PngImagePlugin
from hololab.manifest import load_manifest
from hololab.manifest.schema import InputSpec
from hololab.gateway.port_type_ir import UnionType,compatible,from_legacy
from hololab.gateway.workflows import tags_compatible
from test_megasam_components_040 import mega

ROOT=Path(__file__).parents[1]
class PortsAndFrames(unittest.TestCase):
    def test_manifest_union_and_names(self):
        path=ROOT/'community_packs/megasam-track@0.4.0/manifest.yaml'
        raw=yaml.safe_load(path.read_text()); manifest,_=load_manifest(path)
        expected=[dict(tags=['video-source'],arrayed=False,storage='dir'),
                  dict(tags=['image'],arrayed=True,dim_labels=['frame'],storage='dir')]
        # Schema currently ignores unknown keys: exact raw assertion catches declaration typos too.
        self.assertEqual(raw['inputs']['frames']['any_of'],expected)
        self.assertEqual(set(raw['inputs']['frames']),{'description','any_of'})
        self.assertEqual(manifest.name,'k4d/megasam-track')
        self.assertEqual(manifest.version,'0.4.0')
        self.assertEqual(set(manifest.inputs),{'frames'})
        self.assertEqual(set(manifest.outputs),{'4d_cams','4d_points','4d_images'})
        target=UnionType(variants=[from_legacy(v.tags,v.arrayed,v.dim_labels) for v in manifest.inputs['frames'].any_of])
        for tags,arrayed,labels in [(['video-source'],False,[]),(['image'],True,['frame'])]:
            self.assertTrue(compatible(from_legacy(tags,arrayed,labels),target,tags_compatible))
        self.assertFalse(compatible(from_legacy(['image'],False,[]),target,tags_compatible))
        self.assertFalse(compatible(from_legacy(['video-source'],True,['frame']),target,tags_compatible))
        for invalid in [dict(any_of=expected[:1]),dict(any_of=expected,tags=['image']),
                        dict(any_of=[dict(storage='dir'),expected[1]]),
                        dict(any_of=[dict(tags=['video-source'],storage='wrong'),expected[1]])]:
            with self.assertRaises(ValueError): InputSpec.model_validate(invalid)
        merge,_=load_manifest(ROOT/'community_packs/merge-colmap4d@0.2.0/manifest.yaml')
        self.assertEqual(merge.name,'k4d/merge-colmap4d')
        self.assertEqual(merge.version,'0.2.0')
        xmp,_=load_manifest(ROOT/'community_packs/colmap4d-from-xmp-photos@0.1.0/manifest.yaml')
        self.assertEqual((xmp.name,xmp.version),('k4d/colmap4d-from-xmp-photos','0.1.0'))
        self.assertEqual(set(merge.inputs),set(manifest.outputs))
        for key in merge.inputs: self.assertEqual(merge.inputs[key].tags,manifest.outputs[key].tags)
        from jinja2 import Environment, StrictUndefined
        raw_merge=yaml.safe_load((ROOT/'community_packs/merge-colmap4d@0.2.0/manifest.yaml').read_text())
        script=Environment(undefined=StrictUndefined).from_string(raw_merge['exec']['shell']).render(
            pack_dir='/pack',inputs={key:'/'+key for key in merge.inputs},outputs={'model':'/model'})
        for key in merge.inputs: self.assertIn('/'+key,script)

    def test_frame_timing_precedence_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'source'; source.mkdir(); work=root/'work'; (work/'frames').mkdir(parents=True)
            files=[source/f'{i:03}.png' for i in range(3)]
            for file in files: Image.new('RGB',(4,4)).save(file)
            self.assertEqual(mega.input_kind(source)[0],'images')
            opaque=root/'opaque'; opaque.mkdir()
            Image.new('RGB',(4,4)).save(opaque/'element-0',format='PNG')
            self.assertEqual(mega.input_kind(opaque)[0],'images')
            with self.assertRaisesRegex(ValueError,'frames_fps'): mega.prepare_frames({},source,1,0,work)
            ts,meta=mega.prepare_frames({'frames_fps':25},source,2,0,work)
            self.assertEqual(ts,[0,80000000]); self.assertTrue(meta['conversion']['estimated'])
            def xmp(file,t):
                info=PngImagePlugin.PngInfo()
                info.add_text('XML:com.adobe.xmp',f'<x xmlns:h="https://hololab.dev/ns/rig/1.0/" h:wallClockEpochNs="{t}" h:exposureNs="10" h:timebase="server_epoch"/>')
                Image.new('RGB',(4,4)).save(file,pnginfo=info)
            xmp(files[0],100)
            with self.assertRaisesRegex(ValueError,'Partial XMP'): mega.prepare_frames({'frames_fps':25},source,1,0,work)
            for i,file in enumerate(files): xmp(file,100+i*20)
            ts,meta=mega.prepare_frames({'frames_fps':25},source,1,0,work)
            self.assertEqual(ts,[105,125,145]);self.assertFalse(meta['conversion']['estimated'])
            exposure=root/'exposure.json'
            exposure.write_text(json.dumps(dict(frames_sha256=mega.frame_identity(files),clock_domain='external',frames=[
                dict(decoded_index=i,mid_exposure_ns=1000+i) for i in range(3)])))
            ts,_=mega.prepare_frames({'exposure_metadata_path':str(exposure)},source,1,0,work)
            self.assertEqual(ts,[1000,1001,1002])
            Image.new('RGB',(4,4),(1,2,3)).save(files[0])
            with self.assertRaisesRegex(ValueError,'frames_sha256'): mega.prepare_frames({'exposure_metadata_path':str(exposure),'frames_fps':25},source,1,0,work)
            (source/'movie.mp4').write_bytes(b'not a real movie')
            with self.assertRaisesRegex(ValueError,'Ambiguous'): mega.input_kind(source)

if __name__=='__main__': unittest.main()
