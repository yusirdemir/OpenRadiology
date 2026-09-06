import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { VolumeRenderer, type VolumeLesion, type VolumeSettings } from "../gl/volume";

export function VolumeView({study,series,lesions,active,onSelect}:{study:string;series:string;lesions:VolumeLesion[];active:string;onSelect:(id:string)=>void}) {
  const canvas=useRef<HTMLCanvasElement>(null);const renderer=useRef<VolumeRenderer|null>(null);
  const latest=useRef({lesions,onSelect});latest.current={lesions,onSelect};
  const [error,setError]=useState('');const [ready,setReady]=useState(false);const [context,setContext]=useState(0.5);
  const settings=useRef<VolumeSettings>({yaw:0.12,pitch:0.12,zoom:1.05,context:0.5,active,moving:false,target:null});
  settings.current.context=context;settings.current.active=active;
  const dirty=useRef(true);dirty.current=true;
  const pointer=useRef<{x:number;y:number;startX:number;startY:number;moved:boolean}|null>(null);
  useEffect(()=>{
    let cancelled=false,frame=0;let instance:VolumeRenderer|null=null;let worker:Worker|null=null;
    setReady(false);setError('');
    const observer=new ResizeObserver(()=>{dirty.current=true;});if(canvas.current)observer.observe(canvas.current);
    void api.volumePreview(study,series).then(async(preview)=>{
      const pixels=await new Promise<Float32Array>((resolve,reject)=>{
        worker=new Worker(new URL('../lib/volume.worker.ts',import.meta.url),{type:'module'});
        worker.onmessage=(e)=>{worker?.terminate();resolve(e.data as Float32Array);};
        worker.onerror=(e)=>{worker?.terminate();reject(new Error(e.message));};worker.postMessage(preview.data);
      });
      if(cancelled||!canvas.current)return;
      instance=new VolumeRenderer(canvas.current);renderer.current=instance;instance.setContext(preview,pixels);setReady(true);dirty.current=true;
      const draw=()=>{
        if(cancelled)return;frame=requestAnimationFrame(draw);if(!dirty.current)return;dirty.current=false;
        try{instance!.setLesions(latest.current.lesions);instance!.draw(settings.current);}
        catch(e){setError(String(e));}
      };draw();
    }).catch((e)=>{if(!cancelled)setError(String(e.message??e));});
    return()=>{cancelled=true;worker?.terminate();cancelAnimationFrame(frame);observer.disconnect();instance?.dispose();renderer.current=null;};
  },[study,series]);
  useEffect(()=>{
    const el=canvas.current;if(!el)return;
    const wheel=(e:WheelEvent)=>{e.preventDefault();settings.current.zoom=Math.max(0.5,Math.min(8,settings.current.zoom*Math.exp(-e.deltaY*0.002)));dirty.current=true;};
    el.addEventListener('wheel',wheel,{passive:false});return()=>el.removeEventListener('wheel',wheel);
  },[]);
  function end(e:React.PointerEvent<HTMLCanvasElement>){
    const p=pointer.current;pointer.current=null;settings.current.moving=false;
    if(p&&!p.moved&&renderer.current){
      const rect=e.currentTarget.getBoundingClientRect();
      try{const id=renderer.current.pick(e.clientX-rect.left,e.clientY-rect.top,settings.current);if(id)latest.current.onSelect(id);}catch(error){setError(String(error));}
    }
    dirty.current=true;
  }
  return <section className="absolute inset-0 bg-ink-1000" aria-label="Tüm bulguların 3D hacim görünümü">
    <canvas ref={canvas} className="absolute inset-0 h-full w-full cursor-grab touch-none active:cursor-grabbing" aria-label="Döndürmek için sürükleyin, bulguyu seçmek için renkli hacme tıklayın"
      onPointerDown={(e)=>{e.currentTarget.setPointerCapture(e.pointerId);pointer.current={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false};}}
      onPointerMove={(e)=>{const p=pointer.current;if(!p)return;
        p.moved ||= Math.hypot(e.clientX-p.startX,e.clientY-p.startY)>4;
        if(p.moved){settings.current.moving=true;settings.current.yaw+=(e.clientX-p.x)*0.008;settings.current.pitch=Math.max(-1.45,Math.min(1.45,settings.current.pitch+(e.clientY-p.y)*0.008));dirty.current=true;}
        p.x=e.clientX;p.y=e.clientY;}}
      onPointerUp={end} onPointerCancel={()=>{pointer.current=null;settings.current.moving=false;dirty.current=true;}}
      onLostPointerCapture={()=>{pointer.current=null;settings.current.moving=false;dirty.current=true;}}/>
    {!ready&&!error&&<p role="status" className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-chalk-400">3D BT hacmi hazırlanıyor…</p>}
    {error&&<p role="alert" className="absolute left-5 top-24 max-w-md rounded bg-ink-950/90 p-3 text-xs text-amber-400">{error}</p>}
    <div className="absolute right-4 top-20 flex items-center gap-2 rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2 backdrop-blur">
      <label className="flex items-center gap-2 text-[11px] text-chalk-400">Doku <input aria-label="3D doku opaklığı" type="range" min="0" max="2" step="0.05" value={context} onChange={(e)=>setContext(Number(e.target.value))} className="w-20"/></label>
      <button className="btn text-[11px]" onClick={()=>{Object.assign(settings.current,{yaw:0.12,pitch:0.12,zoom:1.05,target:null});dirty.current=true;}}>Tüm hacim</button>
      <button className="btn text-[11px]" disabled={!lesions.some((l)=>l.findingId===active)} onClick={()=>{const target=lesions.find((l)=>l.findingId===active)?.segmentation.centroid_lps;settings.current.target=target??null;settings.current.zoom=2.5;dirty.current=true;}}>Bulguya yaklaş</button>
    </div>
    <p className="pointer-events-none absolute bottom-4 left-4 text-[11px] text-chalk-500">Sürükle: döndür · Tekerlek: yakınlaştır · Renkli hacme tıkla: vurgula</p>
  </section>;
}
