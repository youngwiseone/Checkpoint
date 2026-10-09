import { Captions, Mic, MonitorSpeaker } from "lucide-react";
export type ChipState = { mic: boolean; micLabel: string; loop: boolean; loopLabel: string; live: boolean; auto: boolean; ai: boolean; ask: boolean };
export default function RecordingChips({ v, onChange, disabled }: { v: ChipState; onChange: (patch: Partial<ChipState>) => void; aiAvailable: boolean; disabled?: boolean; live?: boolean }) {
  return <div className="chips" role="group" aria-label="Recording">
    <button className={`chip ${v.mic ? "on" : ""}`} disabled={disabled} aria-pressed={v.mic} onClick={() => onChange({ mic: !v.mic })}><Mic size={14} /> Microphone {v.mic ? "on" : "off"}</button>
    <button className={`chip ${v.loop ? "on" : ""}`} disabled={disabled} aria-pressed={v.loop} onClick={() => onChange({ loop: !v.loop })}><MonitorSpeaker size={14} /> Computer audio {v.loop ? "on" : "off"}</button>
    <button className={`chip ${v.live ? "on" : ""}`} disabled={disabled || !(v.mic || v.loop)} aria-pressed={v.live} onClick={() => onChange({ live: !v.live })}><Captions size={14} /> {v.live ? "Live transcript" : "Transcribe after"}</button>
  </div>;
}
