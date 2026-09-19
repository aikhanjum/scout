import { useStore } from './store';
import { passes } from './protocol';

// Building view: the summary banner and every space with its checkpoints. Pass/fail is computed
// here from rules.json, never stored. The 3D viewer with pins goes on this screen next.
export function Spaces() {
  const { spaces, rules } = useStore();
  if (!spaces || !rules) return <p className="muted">Could not load data/spaces.json or data/rules.json.</p>;

  const rows = spaces.spaces.map((s) => {
    const cps = s.checkpoints.map((c) => {
      const rule = c.rule ? rules.rules.find((r) => r.id === c.rule) ?? null : null;
      const ok = rule && c.value != null ? passes(rule, c.value) : null; // null = a note, no verdict
      return { ...c, ruleObj: rule, ok };
    });
    return { s, cps, fails: cps.filter((c) => c.ok === false).length };
  });
  const total = {
    spaces: rows.length,
    checkpoints: rows.reduce((n, r) => n + r.cps.length, 0),
    fails: rows.reduce((n, r) => n + r.fails, 0),
  };

  return (
    <>
      <div className="banner">
        <span><b>{total.spaces}</b> spaces</span>
        <span><b>{total.checkpoints}</b> checkpoints</span>
        <span><b className="red">{total.fails}</b> barriers</span>
      </div>
      {rows.length === 0 && (
        <p className="muted">No spaces yet. Put real measurements in <code>data/spaces.json</code> (format: <code>docs/PROTOCOL.md</code> section 7, example: <code>data/spaces.example.json</code>).</p>
      )}
      {rows.map(({ s, cps, fails }) => (
        <section key={s.id} className="space">
          <h3>{s.name} <span className="muted small">{s.model ? '3D model' : 'no model'} · {fails} fail · {cps.length} checkpoints</span></h3>
          <ul className="feed">
            {cps.map((c) => (
              <li key={c.id} className={c.ok === false ? 'fail' : c.ok === true ? 'pass' : 'mark'}>
                <span className="kind">{c.ok === false ? 'FAIL' : c.ok === true ? 'PASS' : 'NOTE'}</span>
                <span>{c.note ?? c.id}{c.value != null && <> · {c.value} {c.unit ?? c.ruleObj?.unit ?? ''}{c.ruleObj && <span className="muted"> (limit {c.ruleObj.limit})</span>}</>}</span>
                <span className="t">{c.source}{c.position ? ' · pin' : ''}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
