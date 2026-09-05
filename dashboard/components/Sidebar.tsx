"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Flags" },
  { href: "/audit", label: "Audit log" },
  { href: "/metrics", label: "Metrics" },
];

export function Sidebar() {
  const pathname = usePathname();

  const isActive = (href: string) =>
    href === "/"
      ? pathname === "/" || pathname.startsWith("/flags")
      : pathname.startsWith(href);

  return (
    <aside className="w-56 shrink-0 border-r border-black/5 bg-white/70 px-4 py-6">
      <div className="px-2">
        <div className="text-lg font-semibold tracking-tight">Ripcord</div>
        <div className="text-xs text-neutral-400">feature flags</div>
      </div>
      <nav className="mt-8 flex flex-col gap-1">
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded-xl px-3 py-2 text-sm font-medium transition ${
              isActive(link.href)
                ? "bg-neutral-900 text-white"
                : "text-neutral-600 hover:bg-neutral-100"
            }`}
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
