import { useEffect, useState } from 'react';
import { useStore } from './store';
import { widthRule } from './protocol';

interface Bucket { start: string; runs: number; frames: number; min_clearance_mm: number | null; fails: number; empty_share: number | null }
interface HistoryDoc { generated_at: string; bucket: string; includes_simulated: boolean; spaces: { space: string; buckets: Bucket[] }[] }

// "Room over time": what tools/upload-run wrote to data/history.json from Tiger Data (PROTOCOL.md section 8).
// No file, or an empty one, means no panel. The browser never talks to the database.
export function History() {
  const [doc, setDoc] = useState<HistoryDoc | null>(null);
  const rules = useStore((s) => s.rules);
  const limit = widthRule(rules)?.limit ?? 860;

  useEffect(() => {
    let alive = true;
    const load = () => fetch('/history.json', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive) setDoc(d && Array.isArray(d.spaces) ? d : null); })
      .catch(() => { if (alive) setDoc(null); });
    load();
    const id = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  if (!doc || doc.spaces.length === 0) return null;
  const when = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  return (
    <section className="sheet history">
      <div className="sheet-head">
        <h2>Room over time</h2>
        <span className="meta">per {doc.bucket}</span>
        {doc.includes_simulated && <span className="tag warn">Simulated</span>}
      </div>
      {doc.spaces.map((s) => (
        <table key={s.space} className="hist">
          <thead><tr><th>{s.space}</th><th>runs</th><th>min</th><th>fails</th><th>empty</th></tr></thead>
          <tbody>
            {s.buckets.map((b) => (
              <tr key={b.start}>
                <td>{when(b.start)}</td>
                <td>{b.runs}</td>
                <td className={b.min_clearance_mm == null ? 'muted' : b.min_clearance_mm < limit ? 'red' : 'green'}>
                  {b.min_clearance_mm == null ? '--' : `${b.min_clearance_mm} mm`}
                </td>
                <td className={b.fails ? 'red' : ''}>{b.fails}</td>
                <td>{b.empty_share == null ? '--' : `${Math.round(b.empty_share * 100)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ))}
    </section>
  );
}
