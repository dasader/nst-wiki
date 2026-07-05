"use client";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "질문하기" },
  { href: "/wiki", label: "위키" },
  { href: "/data", label: "데이터" },
];

export default function Nav() {
  const path = usePathname();
  const active = (href) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <nav className="main">
      {LINKS.map((l) => (
        <a key={l.href} href={l.href} className={active(l.href) ? "active" : ""}>
          {l.label}
        </a>
      ))}
      <a href="http://localhost:8000/" target="_blank" rel="noreferrer" className="ext">
        승인 대시보드
      </a>
    </nav>
  );
}
