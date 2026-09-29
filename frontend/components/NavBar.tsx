"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/lib/api";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/catalog", label: "Catalog" },
  { href: "/market", label: "Market Watch" },
  { href: "/listings", label: "Match Review" },
];

export function NavBar() {
  const pathname = usePathname();
  const router = useRouter();

  if (pathname === "/login") return null;

  return (
    <header className="border-b border-[#1e2836] bg-[#0d1220]">
      <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-emerald-500/15 text-sm font-bold text-emerald-400">
            ₹
          </span>
          <span className="text-sm font-semibold tracking-tight">Pricing DSS</span>
        </Link>

        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((link) => {
            const active =
              link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 transition ${
                  active
                    ? "bg-[#1a2334] text-slate-100"
                    : "text-slate-400 hover:text-slate-200"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <button
          type="button"
          onClick={() => {
            clearToken();
            router.push("/login");
          }}
          className="ml-auto text-xs text-slate-500 hover:text-slate-300"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
