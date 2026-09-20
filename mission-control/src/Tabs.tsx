import { setView, useView, type View } from './App';

const TABS: [View, string][] = [['terminal', 'TERMINAL'], ['map', 'INFERRED POSITION'], ['tiger', 'TIGER DATA'], ['eye', 'EYES']];

// The screen switcher. Same URL, ?view= changes; the browser back button works.
export function Tabs() {
  const view = useView();
  return (
    <nav className="tabs">
      {TABS.map(([v, label]) => (
        <button key={v} className={v === view ? 'tab on' : 'tab'} onClick={() => setView(v)}>{label}</button>
      ))}
    </nav>
  );
}
