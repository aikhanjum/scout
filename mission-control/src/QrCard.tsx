// A QR code a phone scans to get Scout's eyes (?view=eye). The address comes from
// data/live/tunnel.json, written by tools/tunnel.sh. Without a tunnel the page's own origin works
// for a phone on the same hotspot; on localhost there is nothing a phone could reach, so no code.
import { useEffect, useRef, useState } from 'react';
import { toCanvas } from 'qrcode';
import './radar.css';

const POLL_MS = 5000;
const LOCAL = ['localhost', '127.0.0.1'];

const host = (url: string) => { try { return new URL(url).hostname; } catch { return url; } };

export function QrCard() {
  const [tunnel, setTunnel] = useState<string | null>(null);
  const [size, setSize] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let on = true;
    const poll = async () => {
      let url: string | null = null;
      try {
        const r = await fetch('/live/tunnel.json', { cache: 'no-store' });
        if (r.ok) {
          const j = await r.json();
          if (typeof j?.url === 'string' && /^https?:\/\//.test(j.url)) url = j.url.replace(/\/+$/, '');
        }
      } catch { /* 404, or the dev server's index.html: no tunnel */ }
      if (on) setTunnel(url);
    };
    void poll();
    const id = window.setInterval(poll, POLL_MS);
    return () => { on = false; clearInterval(id); };
  }, []);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize(Math.floor(Math.min(r.width, r.height)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const local = LOCAL.includes(location.hostname);
  const target = tunnel ? `${tunnel}/?view=eye` : local ? null : `${location.origin}/?view=eye`;

  useEffect(() => {
    const canvas = cv.current;
    if (!canvas || !target || size < 48) return;
    const dpr = window.devicePixelRatio || 1;
    toCanvas(canvas, target, { margin: 1, width: Math.round(size * dpr), errorCorrectionLevel: 'M', color: { dark: '#d7dde5', light: '#12151a' } })
      .then(() => { canvas.style.width = `${size}px`; canvas.style.height = `${size}px`; })
      .catch(() => { /* nothing to draw */ });
  }, [target, size]);

  return (
    <div className="qr">
      <div className="qr-label"><b>PHONE</b> · scan for Scout's eyes</div>
      <div className="qr-box" ref={box}>
        {target ? <canvas ref={cv} /> : <div className="qr-off">TUNNEL OFF · tools/tunnel.sh</div>}
      </div>
      <div className="qr-host">{target ? host(target) : location.hostname}</div>
    </div>
  );
}
