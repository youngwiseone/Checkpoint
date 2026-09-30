import type { ReactNode } from "react";
import { Camera, Captions, MessageSquare, Mic, MonitorSpeaker, Sparkles } from "lucide-react";

export type ChipState = { mic: boolean; micLabel: string; loop: boolean; loopLabel: string; live: boolean; auto: boolean; ai: boolean; ask: boolean };

/** What gets recorded, as one row of toggles. Used before a session and live during one. */
export default function RecordingChips({ v, onChange, aiAvailable, disabled, live }: { v: ChipState; onChange: (patch: Partial<ChipState>) => void; aiAvailable: boolean; disabled?: boolean; live?: boolean }) {
  const audio = v.mic || v.loop;
  const chip = (on: boolean, label: ReactNode, patch: Partial<ChipState>, title: string, off = false) => (
    <button type="button" className={`chip ${on ? "on" : ""}`} aria-pressed={on} title={title} disabled={disabled || off} onClick={() => onChange(patch)}>{label}</button>
  );
  return (
    <div className="chips" role="group" aria-label="What to record">
      {chip(v.mic, <><Mic size={14} /> {v.mic ? `Mic · ${v.micLabel || "Me"}` : "Mic off"}</>, { mic: !v.mic }, "Record your microphone")}
      {chip(v.loop, <><MonitorSpeaker size={14} /> {v.loop ? `Computer audio · ${v.loopLabel || "Computer audio"}` : "Computer audio off"}</>, { loop: !v.loop }, "Record everything played on the output device (calls, game audio)")}
      {chip(audio && v.live, <><Captions size={14} /> {v.live ? "Live transcript" : "Transcribe after"}</>, { live: !v.live }, "Live transcribes in the background while you play (needed for auto screenshots)", !audio)}
      {chip(v.auto, <><Camera size={14} /> Auto screenshots {v.auto ? "on" : "off"}</>, { auto: !v.auto }, "A local model picks moments where a problem is pointed out (needs live transcript)")}
      {!live && chip(v.ai && aiAvailable, <><Sparkles size={14} /> Organise with AI</>, { ai: !v.ai }, aiAvailable ? "Turn the transcript into cards after the session (local Ollama)" : "Set up Local AI in Settings first", !aiAvailable)}
      {chip(v.ask, <><MessageSquare size={14} /> {v.ask ? "F8 always asks for a note" : "F8 just marks"}</>, { ask: !v.ask }, "With audio on, F8 normally saves a marker without interrupting you")}
    </div>
  );
}
