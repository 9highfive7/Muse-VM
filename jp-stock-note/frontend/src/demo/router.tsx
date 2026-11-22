import { useEffect, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

// Demo build only (swapped in for ../router.tsx by vite.config.ts).
// GitHub Pages serves static files from an arbitrary sub-path and has no SPA
// fallback, so the demo keeps the app's paths in the URL hash instead:
//   <base>/index.html#/stocks/72030   <->   /stocks/72030 in the real app.
// Same exports and behaviour as ../router.tsx.

function current() {
  const h = window.location.hash.replace(/^#/, "") || "/";
  const i = h.indexOf("?");
  return { path: i < 0 ? h : h.slice(0, i), search: i < 0 ? "" : h.slice(i) };
}

export function navigate(to: string) {
  if (window.location.hash.replace(/^#/, "") !== to) window.location.hash = to;
  window.scrollTo(0, 0);
}

export function useLocation() {
  const [loc, setLoc] = useState(current);
  useEffect(() => {
    const onChange = () => setLoc(current());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return loc;
}

export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(href);
  };
  return <a href={`#${href}`} onClick={handle} {...rest} />;
}
