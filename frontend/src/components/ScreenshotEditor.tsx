import { useRef, useState } from "react";
import { Crop, RotateCcw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Modal, useToast } from "./ui";

type Point = { x: number; y: number };
export default function ScreenshotEditor({ captureId, onClose }: { captureId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const image = useRef<HTMLImageElement>(null);
  const [start, setStart] = useState<Point | null>(null);
  const [end, setEnd] = useState<Point | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [mode, setMode] = useState<"crop" | "highlight">("crop");
  const point = (e: React.PointerEvent): Point => {
    const r = image.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };
  const box = start && end ? { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), w: Math.abs(end.x - start.x), h: Math.abs(end.y - start.y) } : null;
  const save = async (reset = false) => {
    if (busy) return;
    setBusy(true);
    try {
      const img = image.current!;
      const x = Math.floor((box?.x ?? 0) * img.naturalWidth), y = Math.floor((box?.y ?? 0) * img.naturalHeight);
      await api.post(`/api/captures/${captureId}/${reset ? "crop/reset" : mode}`, reset ? {} : {
        x, y, width: Math.min(img.naturalWidth - x, Math.max(1, Math.round(box!.w * img.naturalWidth))),
        height: Math.min(img.naturalHeight - y, Math.max(1, Math.round(box!.h * img.naturalHeight))),
      });
      await qc.invalidateQueries();
      onClose();
    } catch (e) { toast("error", (e as Error).message); }
    finally { setBusy(false); }
  };
  return <Modal wide title="Edit screenshot" onClose={() => { if (!busy) onClose(); }} footer={<>
    <button className="btn" disabled={busy || !loaded} onClick={() => void save(true)}><RotateCcw size={14} /> Restore original</button>
    <span className="spacer" /><button className="btn" disabled={busy} onClick={onClose}>Cancel</button>
    <button className="btn primary" disabled={busy || !loaded || !box || box.w < .002 || box.h < .002} onClick={() => void save()}><Crop size={14} /> {busy ? "Saving…" : mode === "crop" ? "Save crop" : "Save highlight"}</button>
  </>}>
    <div className="row" style={{ marginBottom: 12 }}>{(["crop", "highlight"] as const).map((value) => <button key={value} className={`btn ${mode === value ? "primary" : ""}`} disabled={busy} aria-pressed={mode === value} onClick={() => { if (mode !== value) { setMode(value); setLoaded(false); setStart(null); setEnd(null); } }}>{value === "crop" ? "Crop" : "Highlight rectangle"}</button>)}</div>
    <p className="hint" style={{ marginBottom: 12 }}>{mode === "crop" ? "Drag to select the area to keep from the original." : "Drag a rectangle around the problem on the current screenshot."} The original is always preserved.</p>
    <div style={{ position: "relative", width: "fit-content", margin: "auto", touchAction: "none", cursor: "crosshair", lineHeight: 0 }}
      onPointerDown={(e) => { if (!loaded || busy) return; e.currentTarget.setPointerCapture(e.pointerId); const p = point(e); setStart(p); setEnd(p); }}
      onPointerMove={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) setEnd(point(e)); }}
      onPointerUp={(e) => { if (e.currentTarget.hasPointerCapture(e.pointerId)) { setEnd(point(e)); e.currentTarget.releasePointerCapture(e.pointerId); } }}>
      <img key={mode} ref={image} src={`/api/media/captures/${captureId}/${mode === "crop" ? "original" : "image"}`} alt="Screenshot; drag to select an area" draggable={false}
        onLoad={() => setLoaded(true)} onError={() => toast("error", "Couldn't load the original screenshot")}
        style={{ display: "block", maxWidth: "100%", maxHeight: "60vh", userSelect: "none" }} />
      {box && <div style={{ position: "absolute", pointerEvents: "none", left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%`, border: `2px solid ${mode === "highlight" ? "#ff4040" : "var(--accent)"}`, background: mode === "crop" ? "rgba(100,150,255,.16)" : "transparent", boxSizing: "border-box" }} />}
    </div>
  </Modal>;
}
