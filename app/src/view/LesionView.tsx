/** Full-screen 3D scene: every recorded finding of one examination, no segmentation setup or manual correction workflow. */
import { useEffect, useMemo, useState } from 'react';
import type { Finding } from '../lib/findings';
import { colorCss, initialScene, loadScene } from '../lib/scene';
import { VolumeView } from './VolumeView';
import { X } from './icons';
export interface VolumeOption {
  paneIndex:number; studyPath:string; studyUid:string; seriesUid:string;
  /** "Önceki" / "Son" in a comparison; empty for a single read. */
  role:string; dateLabel:string;
}
interface Props {
  options:VolumeOption[]; initial:number; findings:Finding[]; activeId:string|null;
  onSelectFinding:(id:string)=>void; onClose:()=>void;
}
export function LesionView({options,initial,findings,activeId,onSelectFinding,onClose}:Props){
  const [paneIndex,setPaneIndex]=useState(initial);
  const option=options.find((o)=>o.paneIndex===paneIndex)??options[0]!;
  const {studyPath,studyUid,seriesUid,dateLabel}=option;
  const initialEntries=useMemo(()=>initialScene(findings,studyUid),[findings,studyUid]);
  const [entries,setEntries]=useState(initialEntries);
  // The selected finding, if it is present in this examination; else the first that is.
  const [active,setActive]=useState<string>('');
  useEffect(()=>{
    const present=initialEntries.filter((e)=>e.state!=='absent');
    const keep=present.find((e)=>e.finding.id===activeId)??present[0]??initialEntries[0];
    setActive(keep?.finding.id??'');
  },[initialEntries,activeId]);
  // A fallback selection that turned out to have no volume yields to the first finding that has one.
  useEffect(()=>{
    const current=entries.find((e)=>e.finding.id===active);
    if(!current||current.finding.id===activeId||current.lesions.length||current.state==='waiting'||current.state==='running')return;
    const better=entries.find((e)=>e.lesions.length);
    if(better)setActive(better.finding.id);
  },[entries,active,activeId]);
  useEffect(()=>{
    let cancelled=false;setEntries(initialEntries);
    void loadScene(initialEntries,studyPath,studyUid,(entry)=>setEntries((old)=>old.map((e)=>e.finding.id===entry.finding.id?entry:e)),()=>cancelled);
    return()=>{cancelled=true;};
  },[initialEntries,studyPath,studyUid]);
  useEffect(()=>{const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.stopImmediatePropagation();onClose();}};window.addEventListener('keydown',key,{capture:true});return()=>window.removeEventListener('keydown',key,{capture:true});},[onClose]);
  const lesions=useMemo(()=>entries.flatMap((e)=>e.lesions),[entries]);
  const selected=entries.find((e)=>e.finding.id===active);
  const completed=entries.filter((e)=>e.state!=='waiting'&&e.state!=='running').length;
  const current=entries.find((e)=>e.state==='running');
  const dimensions=selected?.lesions.flatMap((l)=>Object.values(l.segmentation.planes).flatMap((s)=>s.map((v)=>v.diameter_mm)))??[];
  const volume=selected?.lesions.reduce((n,l)=>n+l.segmentation.volume_ml,0)??0;
  const closures=selected?.lesions.flatMap((l)=>l.segmentation.pleural_closure?.slices??[])??[];
  const pick=(id:string)=>{setActive(id);onSelectFinding(id);};
  return <div className="fixed inset-0 z-40 bg-ink-1000" role="dialog" aria-modal="true" aria-label="Tam ekran 3D bulgular">
    <VolumeView key={`${studyPath}|${seriesUid}`} study={studyPath} series={seriesUid} lesions={lesions} active={active} onSelect={pick}/>
    <header className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-4 border-b border-white/10 bg-ink-1000/65 px-5 py-3.5 backdrop-blur">
      <div className="min-w-0 flex-1">
        <p className="eyebrow text-chalk-500">3D hacim{option.role?` · ${option.role.toLocaleLowerCase('tr')} tetkik`:''} · {dateLabel}</p>
        <h2 className="mt-1 truncate text-sm text-chalk-100">{selected?.finding.headline??'Tüm bulgular'}</h2>
      </div>
      {options.length>1&&<div className="pointer-events-auto flex shrink-0 overflow-hidden rounded-lg border border-white/15" role="group" aria-label="Hangi tetkik">
        {options.map((o)=><button key={o.paneIndex} type="button" aria-pressed={o.paneIndex===paneIndex} onClick={()=>setPaneIndex(o.paneIndex)}
          className={`px-3 py-1.5 text-[11.5px] transition-colors ${o.paneIndex===paneIndex?'bg-white/15 text-chalk-100':'text-chalk-400 hover:bg-white/5'}`}>
          <span className="eyebrow mr-1.5 text-[9.5px]">{o.role}</span>{o.dateLabel}</button>)}
      </div>}
      <span className="shrink-0 text-[11px] text-chalk-500">{completed}/{entries.length} bulgu işlendi</span>
      <button className="btn pointer-events-auto" onClick={onClose} aria-label="3D görünümü kapat (Esc)" title="Kapat (Esc)"><X size={17}/></button>
    </header>
    <div className="absolute bottom-14 left-4 flex max-h-[55vh] w-[260px] flex-col gap-1 overflow-y-auto rounded-xl border border-white/10 bg-ink-1000/75 p-2 backdrop-blur">
      {entries.map((entry)=>{
        const focused=entry.finding.id===active;
        const label=entry.state==='waiting'?'Sırada':entry.state==='running'?'Hesaplanıyor':entry.state==='absent'?'Bu tetkikte yok':entry.state==='unavailable'?'Sınır belirlenemedi':entry.state==='limited'?'Otomatik sınır · sınırlı':'Otomatik sınır';
        return <button key={entry.finding.id} aria-pressed={focused} disabled={entry.state==='absent'} onClick={()=>pick(entry.finding.id)}
          className={`flex items-start gap-2 rounded-lg px-2.5 py-2 text-left transition-colors disabled:opacity-50 ${focused?'bg-white/10':'hover:bg-white/5'}`} title={entry.note||entry.finding.technical}>
          <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{background:colorCss(entry.color),opacity:focused?1:.5}}/>
          <span className="min-w-0"><span className={`block line-clamp-2 text-[11px] leading-relaxed ${focused?'text-chalk-100':'text-chalk-500'}`}>{entry.finding.headline}</span>
            <span className="mt-0.5 block text-[10px] text-chalk-600">{label}</span></span>
        </button>;
      })}
    </div>
    {selected&&<div className="pointer-events-none absolute bottom-14 right-4 max-w-xs rounded-xl border border-white/10 bg-ink-1000/75 px-4 py-3 backdrop-blur">
      <p className="eyebrow" style={{color:colorCss(selected.color)}}>Seçili bulgu</p>
      {volume>0?<><p className="mt-1 readout text-xl text-chalk-100">{Math.max(...dimensions).toFixed(1)} <span className="text-xs text-chalk-500">mm</span></p>
        <p className="mt-1 text-[11px] text-chalk-400">{volume.toFixed(2)} mL · otomatik maske</p></>:<p className="mt-2 text-[11px] text-chalk-400">{selected.state==='running'?'Hacim hesaplanıyor…':selected.note||'Hacim hazırlanıyor'}</p>}
      {closures.length>0&&<p className="mt-2 text-[10px] text-amber-300">Plevral yay: {closures.length} kesitte sınır göğüs duvarı eğrisiyle kapatıldı · en uzun kord {Math.max(...closures.map((c)=>c.chord_mm)).toFixed(0)} mm</p>}
      {selected.state==='limited'&&<p className="mt-2 text-[10px] text-amber-400">Sınırın bir bölümünde model belirsizliği var.</p>}
    </div>}
    {current&&<p role="status" className="pointer-events-none absolute bottom-4 right-4 text-[11px] text-chalk-500">Bulgular otomatik hesaplanıyor…</p>}
  </div>;
}
