import { api } from '../api/client';
import type { LesionSegmentation, SeriesMeta, Voxel } from '../api/types';
import type { Finding } from './findings';
import { profileFor, segmentLesion } from './lesions';
import type { VolumeLesion } from '../gl/volume';
export const FINDING_COLORS: [number,number,number][] = [
  [1,.31,.17],[.12,.84,1],[.8,.49,1],[.35,.95,.56],[1,.74,.18],[1,.37,.7],[.43,.65,1],[.7,.91,.23],
];
export function colorCss(color: number[]) { return `rgb(${color.map((c)=>Math.round(c*255)).join(',')})`; }
export interface SceneFinding {
  finding: Finding; color: [number,number,number]; state: 'waiting'|'running'|'ready'|'limited'|'unavailable'|'absent';
  lesions: VolumeLesion[]; note: string;
}
export function physicalPoint(meta: SeriesMeta, z: number, r: number, c: number): Voxel {
  const o=meta.geometry.origin_lps;
  if(!o)throw new Error('Hasta koordinat başlangıcı eksik');
  const iop=meta.geometry.iop;
  const normal=[iop[1]!*iop[5]!-iop[2]!*iop[4]!,iop[2]!*iop[3]!-iop[0]!*iop[5]!,iop[0]!*iop[4]!-iop[1]!*iop[3]!];
  return o.map((v,i)=>v+c*meta.geometry.pixel_spacing_mm[1]*iop[i]!+r*meta.geometry.pixel_spacing_mm[0]*iop[i+3]!+z*meta.geometry.slice_spacing_mm*normal[i]!) as Voxel;
}
export function initialScene(findings:Finding[],studyUid:string):SceneFinding[] {
  return findings.filter((f)=>Boolean(f.atStudy[studyUid])).map((finding,i)=>({finding,color:FINDING_COLORS[i%FINDING_COLORS.length]!,
    state:finding.atStudy[studyUid]?.presence==='present'?'waiting':'absent',lesions:[],note:''}));
}
/** All recorded localised findings are processed automatically, one job at a time.
 * The same physical reference repeated in another reconstruction is not a second lesion. */
export async function loadScene(entries:SceneFinding[],studyPath:string,studyUid:string,onEntry:(entry:SceneFinding)=>void,cancelled:()=>boolean) {
  const metas=new Map<string,SeriesMeta>();
  for(const entry of entries){
    if(cancelled())return;
    if(entry.state==='absent'){onEntry(entry);continue;}
    onEntry({...entry,state:'running'});
    const at=entry.finding.atStudy[studyUid]!;
    const references=entry.finding.claim.refs.filter((r)=>r.study_uid===studyUid&&r.row!==undefined&&r.col!==undefined);
    if(!references.length&&at.ref?.row!==undefined&&at.ref.col!==undefined)references.push(at.ref);
    const points:Voxel[]=[];const results:VolumeLesion[]=[];const errors:string[]=[];
    for(const ref of references){
      if(cancelled())return;
      try{
        let meta=metas.get(ref.series_uid);
        if(!meta){meta=await api.seriesMeta(studyPath,ref.series_uid);metas.set(ref.series_uid,meta);}
        const z=meta.sop_uids.indexOf(ref.sop_uid);if(z<0)throw new Error('Kayıtlı kesit bu seride bulunamadı');
        const point=physicalPoint(meta,z,ref.row!,ref.col!);
        if(points.some((p)=>Math.hypot(...p.map((v,i)=>v-point[i]!))<3))continue;
        points.push(point);
        const seg:LesionSegmentation=await segmentLesion({studyPath,seriesUid:ref.series_uid,index:z,row:ref.row!,col:ref.col!,
          profile:profileFor(entry.finding.technical||entry.finding.headline)});
        if(seg.voxels)results.push({id:`${entry.finding.id}:${results.length}`,findingId:entry.finding.id,color:entry.color,segmentation:seg});
        else errors.push(seg.reasons.join(' · ')||'Kapalı anatomik sınır bulunamadı');
      }catch(error){errors.push(error instanceof Error?error.message:String(error));}
    }
    const limited=results.some((r)=>r.segmentation.status==='needs-review');
    if(!cancelled())onEntry({...entry,lesions:results,state:results.length?(limited||errors.length?'limited':'ready'):'unavailable',
      note:errors.join(' · ')||(!references.length?'Kayıtta hacimsel hedef koordinatı yok':limited?'Temas yüzeyinde otomatik model kullanıldı':'')});
  }
}
