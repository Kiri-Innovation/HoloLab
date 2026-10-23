import {expect,it} from 'vitest';
import {storedPixelBlob} from './imageCropPixels';
const ascii=(s:string)=>Array.from(s,c=>c.charCodeAt(0));
it('removes JPEG EXIF but preserves other segments and compressed scan',async()=>{
 const scan=[255,218,0,2,1,2,3,255,217];
 const bytes=new Uint8Array([255,216,255,225,0,10,...ascii('Exif\0\0'),1,2,255,226,0,4,4,5,...scan]);
 expect([...new Uint8Array(await storedPixelBlob(bytes).arrayBuffer())]).toEqual([255,216,255,226,0,4,4,5,...scan]);
});
it('removes PNG eXIf chunks without touching other chunks',async()=>{
 const header=[137,...ascii('PNG'),13,10,26,10], chunk=[0,0,0,0,...ascii('IEND'),0,0,0,0];
 const bytes=new Uint8Array([...header,0,0,0,1,...ascii('eXIf'),6,0,0,0,0,...chunk]);
 expect([...new Uint8Array(await storedPixelBlob(bytes).arrayBuffer())]).toEqual([...header,...chunk]);
});
it('removes WebP EXIF and repairs size and metadata flag',async()=>{
 const bytes=new Uint8Array([...ascii('RIFF'),26,0,0,0,...ascii('WEBP'),...ascii('VP8X'),2,0,0,0,8,0,...ascii('EXIF'),1,0,0,0,6,0]);
 const result=new Uint8Array(await storedPixelBlob(bytes).arrayBuffer());
 expect(result.length).toBe(22);expect(result[4]).toBe(14);expect(result[20]).toBe(0);
});
it('rejects corrupt lengths and unsupported formats',()=>{
 expect(()=>storedPixelBlob(new Uint8Array([255,216,255,225,255,255]))).toThrow();
 expect(()=>storedPixelBlob(new Uint8Array(12))).toThrow();
});
