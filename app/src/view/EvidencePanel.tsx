/**
 * The evidence panel.
 *
 * A finding, the pixel it was seen on, the number that was measured, the hash
 * of the file that number came from, and the sentence a patient can read --
 * one object, in one place. The interface deliberately cannot save a finding
 * without an address, and deliberately cannot type a measurement: the only way
 * a number gets here is by coming back from the engine with its evidence file.
 */
import { useMemo, useState } from "react";
import type {
  AttestationCoverage,
  Claim,
  LocaleBundle,
  MeasureResult,
  Ref,
  Region,
  RegionStatus,
  SessionPage,
  SessionStudy,
} from "../api/types";
import { AddressChip, MeasurementChip, STATUS_LABEL_TR } from "./primitives";

export type EvidenceTab = "region" | "claims" | "measure" | "pages";

interface Props {
  study: SessionStudy | undefined;
  locale: LocaleBundle | null;
  activeRegionKey: string | null;
  claims: Claim[];
  activeClaimId: string | null;
  lastMeasurement: MeasureResult | null;
  measuring: boolean;
  measureError: string | null;
  canAddress: boolean;
  seriesNumberOf: (seriesUid: string) => string | undefined;
  onRegionPatch: (key: string, patch: Partial<Region>) => void;
  onAddRegionRef: (key: string) => void;
  onDropRegionRef: (key: string, index: number) => void;
  onJump: (reference: Ref) => void;
  onSelectClaim: (id: string | null) => void;
  onClaimPatch: (claim: Claim) => void;
  onClaimRemove: (id: string) => void;
  onNewClaimFromMeasurement: () => void;
  onAttachMeasurement: (claimId: string) => void;
  pages: SessionPage[];
  attestation: AttestationCoverage | null;
  onOpenPage: (page: SessionPage) => void;
  onRender: () => void;
}

const STATUSES: RegionStatus[] = ["finding", "no_finding", "limited", "not_covered"];

export function EvidencePanel(props: Props) {
  const [tab, setTab] = useState<EvidenceTab>("region");
  const region = props.activeRegionKey ? props.study?.regions[props.activeRegionKey] : undefined;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-7 shrink-0 border-b border-[var(--hairline)]">
        {(
          [
            ["region", "Bölge"],
            ["claims", `Bulgular${props.claims.length ? ` (${props.claims.length})` : ""}`],
            ["measure", "Ölçüm"],
            ["pages", `Sayfalar${props.pages.length ? ` (${props.pages.length})` : ""}`],
          ] as [EvidenceTab, string][]
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`flex-1 border-r border-[var(--hairline)] text-[11px] tracking-wide transition-colors ${
              tab === id ? "bg-ink-800 text-amber-400" : "text-chalk-500 hover:text-chalk-300"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="scroll-y min-h-0 flex-1">
        {tab === "region" && (
          <RegionEditor
            regionKey={props.activeRegionKey}
            region={region}
            locale={props.locale}
            canAddress={props.canAddress}
            seriesNumberOf={props.seriesNumberOf}
            onPatch={props.onRegionPatch}
            onAddRef={props.onAddRegionRef}
            onDropRef={props.onDropRegionRef}
            onJump={props.onJump}
          />
        )}
        {tab === "claims" && (
          <ClaimList
            claims={props.claims}
            activeClaimId={props.activeClaimId}
            seriesNumberOf={props.seriesNumberOf}
            onSelect={props.onSelectClaim}
            onPatch={props.onClaimPatch}
            onRemove={props.onClaimRemove}
            onJump={props.onJump}
            onAttach={props.onAttachMeasurement}
            hasMeasurement={props.lastMeasurement !== null}
          />
        )}
        {tab === "measure" && (
          <MeasureReadout
            result={props.lastMeasurement}
            busy={props.measuring}
            error={props.measureError}
            onNewClaim={props.onNewClaimFromMeasurement}
          />
        )}
        {tab === "pages" && (
          <PageList
            pages={props.pages}
            attestation={props.attestation}
            onOpen={props.onOpenPage}
            onRender={props.onRender}
          />
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- region
function RegionEditor({
  regionKey,
  region,
  locale,
  canAddress,
  seriesNumberOf,
  onPatch,
  onAddRef,
  onDropRef,
  onJump,
}: {
  regionKey: string | null;
  region: Region | undefined;
  locale: LocaleBundle | null;
  canAddress: boolean;
  seriesNumberOf: (uid: string) => string | undefined;
  onPatch: (key: string, patch: Partial<Region>) => void;
  onAddRef: (key: string) => void;
  onDropRef: (key: string, index: number) => void;
  onJump: (reference: Ref) => void;
}) {
  if (!regionKey || !region) {
    return <p className="p-3 text-xs text-chalk-600">Soldaki listeden bir anatomik bölge seçin.</p>;
  }
  const bare = regionKey.slice(regionKey.indexOf(":") + 1);
  const needsAddress = region.status === "finding" && region.refs.length === 0;

  return (
    <div className="space-y-3 p-2.5">
      <h3 className="text-[13px] font-semibold text-chalk-100">{locale?.regions?.[bare] ?? bare}</h3>

      <div className="grid grid-cols-2 gap-1">
        {STATUSES.map((status) => (
          <button
            key={status}
            type="button"
            className={`btn justify-start ${region.status === status ? "btn-active" : ""}`}
            onClick={() => onPatch(regionKey, { status })}
          >
            <span className={`tick tick-${status} h-3`} />
            {STATUS_LABEL_TR[status]}
          </button>
        ))}
      </div>

      <Labelled label="Hekim ifadesi">
        <textarea
          className="field min-h-[64px] resize-y"
          value={region.text}
          placeholder="Ne görüldü, hangi seride, hangi ölçüyle."
          onChange={(e) => onPatch(regionKey, { text: e.target.value })}
        />
      </Labelled>

      <Labelled label="Hasta dilinde">
        <textarea
          className="field min-h-[52px] resize-y"
          value={region.explanation}
          placeholder="Aynı bulgunun gündelik dildeki karşılığı."
          onChange={(e) => onPatch(regionKey, { explanation: e.target.value })}
        />
      </Labelled>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="rail-label">Adresler</span>
          <button
            type="button"
            className="btn"
            disabled={!canAddress}
            title={canAddress ? "Görüntüde seçili pikseli adres olarak ekle" : "Önce görüntüde bir piksel seçin"}
            onClick={() => onAddRef(regionKey)}
          >
            + geçerli piksel
          </button>
        </div>
        {region.refs.length === 0 ? (
          <p className={`text-[11px] ${needsAddress ? "text-alarm-400" : "text-chalk-600"}`}>
            {needsAddress
              ? "Bulgu, adres olmadan kaydedilemez: görüntüde ilgili pikseli seçip ekleyin."
              : "Henüz adres eklenmedi."}
          </p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {region.refs.map((reference, i) => (
              <span key={`${reference.sop_uid}-${i}`} className="inline-flex items-center gap-0.5">
                <AddressChip
                  reference={reference}
                  seriesNumber={seriesNumberOf(reference.series_uid)}
                  onJump={onJump}
                />
                <button
                  type="button"
                  className="px-1 text-[11px] text-chalk-600 hover:text-alarm-400"
                  title="Adresi kaldır"
                  onClick={() => onDropRef(regionKey, i)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------- claims
function ClaimList({
  claims,
  activeClaimId,
  seriesNumberOf,
  onSelect,
  onPatch,
  onRemove,
  onJump,
  onAttach,
  hasMeasurement,
}: {
  claims: Claim[];
  activeClaimId: string | null;
  seriesNumberOf: (uid: string) => string | undefined;
  onSelect: (id: string | null) => void;
  onPatch: (claim: Claim) => void;
  onRemove: (id: string) => void;
  onJump: (reference: Ref) => void;
  onAttach: (claimId: string) => void;
  hasMeasurement: boolean;
}) {
  if (!claims.length) {
    return (
      <p className="p-3 text-xs text-chalk-600">
        Henüz bulgu yok. Bir ölçüm yapın ve “Ölçüm” sekmesinden bulguya dönüştürün.
      </p>
    );
  }
  return (
    <div className="divide-y divide-[var(--hairline)]">
      {claims.map((claim) => {
        const open = claim.id === activeClaimId;
        return (
          <article key={claim.id} className="px-2.5 py-2">
            <button
              type="button"
              className="flex w-full items-start gap-2 text-left"
              onClick={() => onSelect(open ? null : claim.id)}
            >
              <span
                className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                  claim.priority === "important" ? "bg-alarm-400" : claim.priority === "routine" ? "bg-amber-400" : "bg-ink-500"
                }`}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-[12px] leading-snug text-chalk-100">{claim.text || claim.id}</span>
                <span className="readout mt-0.5 block text-[10px] text-chalk-600">
                  {claim.confidence} · {claim.refs.length} adres
                  {claim.measurements?.length ? ` · ${claim.measurements.length} ölçüm` : ""}
                  {claim.comparison ? ` · ${claim.comparison.status}` : ""}
                </span>
              </span>
            </button>

            {open && (
              <div className="mt-2 space-y-2 border-l border-[var(--hairline)] pl-2.5">
                <Labelled label="Hekim ifadesi">
                  <textarea
                    className="field min-h-[52px] resize-y"
                    value={claim.text}
                    onChange={(e) => onPatch({ ...claim, text: e.target.value })}
                  />
                </Labelled>

                <div className="grid grid-cols-2 gap-1.5">
                  <Labelled label="Güven">
                    <select
                      className="field"
                      value={claim.confidence}
                      onChange={(e) => onPatch({ ...claim, confidence: e.target.value as Claim["confidence"] })}
                    >
                      <option value="low">düşük</option>
                      <option value="moderate">orta</option>
                      <option value="high">yüksek</option>
                    </select>
                  </Labelled>
                  <Labelled label="Öncelik">
                    <select
                      className="field"
                      value={claim.priority}
                      onChange={(e) => onPatch({ ...claim, priority: e.target.value as Claim["priority"] })}
                    >
                      <option value="important">önemli</option>
                      <option value="routine">rutin</option>
                      <option value="incidental">rastlantısal</option>
                    </select>
                  </Labelled>
                </div>

                {claim.measurements?.length ? (
                  <div>
                    <span className="rail-label">Ölçümler</span>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {claim.measurements.map((m, i) => (
                        <MeasurementChip key={i} value={m.value} unit={m.unit} sha256={m.sha256} method={m.method} />
                      ))}
                    </div>
                  </div>
                ) : null}

                <div>
                  <span className="rail-label">Adresler</span>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {claim.refs.map((reference, i) => (
                      <AddressChip
                        key={i}
                        reference={reference}
                        seriesNumber={seriesNumberOf(reference.series_uid)}
                        onJump={onJump}
                      />
                    ))}
                  </div>
                </div>

                <Labelled label="Hasta dilinde: ne anlama geliyor">
                  <textarea
                    className="field min-h-[44px] resize-y"
                    value={claim.patient.meaning}
                    onChange={(e) => onPatch({ ...claim, patient: { ...claim.patient, meaning: e.target.value } })}
                  />
                </Labelled>
                <Labelled label="Hasta dilinde: belirsizlik">
                  <textarea
                    className="field min-h-[38px] resize-y"
                    value={claim.patient.uncertainty}
                    onChange={(e) => onPatch({ ...claim, patient: { ...claim.patient, uncertainty: e.target.value } })}
                  />
                </Labelled>

                <div className="flex gap-1">
                  <button
                    type="button"
                    className="btn"
                    disabled={!hasMeasurement}
                    title={hasMeasurement ? "Son ölçümü bu bulguya ekle" : "Önce bir ölçüm yapın"}
                    onClick={() => onAttach(claim.id)}
                  >
                    + son ölçüm
                  </button>
                  <button type="button" className="btn text-alarm-400" onClick={() => onRemove(claim.id)}>
                    Sil
                  </button>
                </div>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ measure
function MeasureReadout({
  result,
  busy,
  error,
  onNewClaim,
}: {
  result: MeasureResult | null;
  busy: boolean;
  error: string | null;
  onNewClaim: () => void;
}) {
  const rows = useMemo(() => (result ? summarise(result) : []), [result]);

  if (busy) return <p className="p-3 text-xs text-chalk-500">Motor ölçüyor…</p>;
  if (error) return <p className="p-3 text-xs text-alarm-400">{error}</p>;
  if (!result) {
    return (
      <p className="p-3 text-xs text-chalk-600">
        Bir ölçüm aracı seçip görüntüde nokta belirleyin. Ekrandaki her sayı motorun ölçtüğü ve imzaladığı değerdir;
        arayüz kendi hesapladığı bir rakamı göstermez.
      </p>
    );
  }

  return (
    <div className="space-y-2.5 p-2.5">
      <div className="flex flex-wrap gap-1">
        {rows.map((row, i) => (
          <MeasurementChip key={i} value={row.value} unit={row.unit} method={row.method} sha256={result.evidence?.sha256} />
        ))}
      </div>

      {result.data.warnings.map((warning, i) => (
        <p key={i} className="text-[11px] text-caution-400">
          {warning}
        </p>
      ))}
      {result.data.notes.map((note, i) => (
        <p key={i} className="text-[11px] text-chalk-500">
          {note}
        </p>
      ))}

      {result.evidence && (
        <div className="rounded-[2px] border border-[var(--hairline)] bg-ink-950 p-2">
          <div className="rail-label mb-1">Kanıt dosyası</div>
          <p className="readout break-all text-[10px] text-chalk-500">{result.evidence.path}</p>
          <p className="readout mt-1 break-all text-[10px] text-attest-400">sha256 {result.evidence.sha256}</p>
        </div>
      )}

      <details>
        <summary className="cursor-pointer text-[11px] text-chalk-600 hover:text-chalk-300">Motor çıktısı</summary>
        <pre className="readout mt-1 whitespace-pre-wrap break-all text-[10px] leading-relaxed text-chalk-500">
          {result.text}
        </pre>
        <p className="readout mt-1 break-all text-[10px] text-chalk-600">$ {result.command.join(" ")}</p>
      </details>

      <button type="button" className="btn btn-primary w-full" onClick={onNewClaim}>
        Bu ölçümden bulgu oluştur
      </button>
    </div>
  );
}

/** Pull the display-worthy numbers out of an engine measurement report. */
function summarise(result: MeasureResult): { value: number; unit: string; method: string }[] {
  const unit = result.data.unit ?? "HU";
  const out: { value: number; unit: string; method: string }[] = [];
  for (const entry of result.data.results) {
    if (entry.kind === "distance" && typeof entry.value_mm === "number") {
      out.push({ value: entry.value_mm, unit: "mm", method: String(entry.method ?? "mesafe") });
    } else if (entry.kind === "roi" && typeof entry.mean === "number") {
      out.push({ value: entry.mean, unit, method: `ROI ortalama (n=${entry.n})` });
      if (typeof entry.suv_max === "number") out.push({ value: entry.suv_max, unit, method: "SUVmax" });
    } else if (entry.kind === "region" || entry.kind === "region3d") {
      for (const [key, label] of [
        ["long_axis_mm", "uzun eksen"],
        ["short_axis_mm", "kısa eksen"],
        ["reported_mm", "raporlanan çap"],
        ["craniocaudal_extent_mm", "kraniyokaudal uzanım"],
      ] as const) {
        const value = entry[key];
        if (typeof value === "number") out.push({ value, unit: "mm", method: label });
      }
    } else if (entry.kind === "extent" && typeof entry.craniocaudal_extent_mm === "number") {
      out.push({ value: entry.craniocaudal_extent_mm, unit: "mm", method: "kraniyokaudal uzanım" });
    }
  }
  return out;
}

function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="rail-label mb-1 block">{label}</span>
      {children}
    </label>
  );
}


// -------------------------------------------------------------------- pages
/**
 * The rendered sheets, and whether looking at them has actually happened.
 *
 * `reviewed` is never a checkbox here. It is set by the server from view
 * attestations and cleared again for any page none covers, so the only way to
 * move a page into the "read" column is to open it and read it.
 */
function PageList({
  pages,
  attestation,
  onOpen,
  onRender,
}: {
  pages: SessionPage[];
  attestation: AttestationCoverage | null;
  onOpen: (page: SessionPage) => void;
  onRender: () => void;
}) {
  const attested = new Map((attestation?.pages ?? []).map((row) => [row.path, row]));
  if (!pages.length) {
    return (
      <div className="space-y-3 p-2.5">
        <p className="text-xs leading-relaxed text-chalk-600">
          Bu oturumda henüz render edilmiş sayfa yok. Rapor, hangi kesitlerin sistematik olarak
          tarandığını sayfalar üzerinden kanıtlar.
        </p>
        <button type="button" className="btn btn-primary w-full" onClick={onRender}>
          Bu seriden sayfa üret
        </button>
      </div>
    );
  }
  const done = pages.filter((page) => page.reviewed).length;
  return (
    <div className="p-2.5">
      <div className="mb-2 flex items-center justify-between">
        <span className="readout text-[11px] text-chalk-300">
          {done}
          <span className="text-chalk-600">/{pages.length} okundu</span>
        </span>
        <button type="button" className="btn" onClick={onRender}>
          + sayfa üret
        </button>
      </div>
      <ul className="space-y-0.5">
        {pages.map((page) => {
          const row = attested.get(page.path);
          return (
            <li key={page.path}>
              <button
                type="button"
                onClick={() => onOpen(page)}
                className="flex w-full items-center gap-2 rounded-[2px] px-1.5 py-1 text-left hover:bg-ink-850"
                title={row?.reason ?? ""}
              >
                <span className={page.reviewed ? "text-attest-400" : "text-chalk-600"}>
                  {page.reviewed ? "✓" : "○"}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="readout block truncate text-[11px] text-chalk-100">
                    {page.path.split("/").pop()}
                  </span>
                  <span className="block truncate text-[10px] text-chalk-600">
                    {page.purpose || "amaç belirtilmemiş"} · {page.sources.length} kesit
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
