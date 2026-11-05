"""Legacy XMP migration parity, without altering its frozen predecessor."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).parents[1]/'community_packs'
OLD=ROOT/'merge-colmap4d@0.1.5/merge_colmap4d.py'
NEW=ROOT/'frames-to-colmap4d@0.1.0/merge_colmap4d.py'

def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod

class XMPParity(unittest.TestCase):
    def test_algorithm_identity(self):
        self.assertEqual(OLD.read_text().replace('col4d/merge-colmap4d@0.1.5','col4d/frames-to-colmap4d@0.1.0'),NEW.read_text())

    def test_full_legacy_assembly(self):
        old,new=load(OLD,'oldxmp'),load(NEW,'newxmp')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cams,points,images=[root/k for k in ('cams','points','images')]
            for p in (cams/'g000000',points/'g000000',images): p.mkdir(parents=True)
            (cams/'g000000/cameras.txt').write_text('7 PINHOLE 4 4 4 4 2 2\n')
            (cams/'g000000/images.txt').write_text('11 1 0 0 0 0 0 0 7 cam0.jpg\n2 2 101\n')
            (points/'g000000/points3D.txt').write_text('101 0 0 2 255 0 0 0 11 0\n')
            values=dict(aeMode='MANUAL',alias='cam0',capId='id',clockAgeMs='1',clockOffsetMs='2',decodedIndex='3',exposureNs='3000000',focusDistance='1.0',iso='100',sensorTimestampNs='1000',source='sei',syncVersion='7',timebase='server_epoch',wallClockEpochNs='1000000000')
            attrs=' '.join(f'hololab:{k}="{v}"' for k,v in values.items())
            xml=f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns/"><rdf:Description xmlns:hololab="{old.HOLOLAB_XMP_NS}" {attrs}/></rdf:RDF></x:xmpmeta>'.encode()
            payload=old.XMP_APP1_HEADER+xml
            (images/'g000000__cam0.jpg').write_bytes(b'\xff\xd8\xff\xe1'+(len(payload)+2).to_bytes(2,'big')+payload+b'\xff\xda\x00\x02scan\xff\xd9')
            for module,name in ((old,'old'),(new,'new')):
                module.merge(points,images,cams,root/name)
            for file in (root/'old/sparse/0').iterdir():
                other=root/'new/sparse/0'/file.name
                if file.name=='time_meta.json':
                    a,b=json.loads(file.read_text()),json.loads(other.read_text())
                    a['conversion']['source']=b['conversion']['source']; self.assertEqual(a,b)
                else: self.assertEqual(file.read_bytes(),other.read_bytes())

if __name__=='__main__': unittest.main()
