"use client";

import { useEffect, useState } from "react";
import { ErrorBox, Spinner, StockPill } from "@/components/ui";
import { apiFetch, type Listing, type Page } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import { useRequireAuth } from "@/lib/useRequireAuth";

const STATUSES = [
  { key: "", label: "All" },
  { key: "auto_accepted", label: "Auto-accepted" },
  { key: "pending", label: "Pending" },
  { key: "needs_review", label: "Needs review" },
  { key: "confirmed", label: "Confirmed" },
  { key: "rejected", label: "Rejected" },
];

function MatchBadge({ listing }: { listing: Listing }) {
  const status = (listing.match_status || "").toLowerCase();
  const tone =
    status === "auto_accepted" || status === "confirmed"
      ? "bg-emerald-500/15 text-emerald-300"
      : status === "rejected"
        ? "bg-red-500/15 text-red-300"
        : "bg-slate-500/15 text-slate-300";
  return <span className={`pill ${tone}`}>{status.replace(/_/g, " ")}</span>;
}

export default function ListingsPage() {
  const ready = useRequireAuth();
  const [rows, setRows] = useState<Listing[] | null>(null);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ready) return;
    const query = new URLSearchParams({ limit: "200" });
    if (status) query.set("match_status", status);
    apiFetch<Page<Listing>>(`/api/market/listings?${query}`)
      .then((page) => {
        setRows(page.items);
        setTotal(page.total);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [ready, status]);

  if (!ready) return null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">Match Review</h1>
        <p className="mt-1 text-sm text-slate-400">
          {total} competitor listings. Exact GTIN matches are auto-accepted; the rest
          await the embedding matcher and human review.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        {STATUSES.map((option) => (
          <button
            key={option.key || "all"}
            type="button"
            onClick={() => setStatus(option.key)}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${
              status === option.key
                ? "bg-emerald-500 text-slate-950"
                : "bg-[#151d2b] text-slate-400 hover:text-slate-200"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error && <ErrorBox message={error} />}

      {!rows && !error && <Spinner />}

      {rows && !error && (
        <div className="card overflow-x-auto">
          <table className="data">
            <thead>
              <tr>
                <th>Competitor</th>
                <th>Listing title</th>
                <th>GTIN</th>
                <th>Matched SKU</th>
                <th>Method</th>
                <th>Status</th>
                <th className="text-right">Latest price</th>
                <th>Stock</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((listing) => (
                <tr key={listing.id}>
                  <td className="text-slate-400">{listing.competitor_name}</td>
                  <td className="max-w-md truncate text-slate-300" title={listing.title}>
                    {listing.title}
                  </td>
                  <td className="font-mono text-xs text-slate-500">
                    {listing.gtin ?? "—"}
                  </td>
                  <td className="font-mono text-xs text-slate-300">
                    {listing.matched_product_sku ?? "—"}
                  </td>
                  <td className="text-xs text-slate-500">
                    {listing.match_method.replace(/_/g, " ").toLowerCase()}
                  </td>
                  <td>
                    <MatchBadge listing={listing} />
                  </td>
                  <td className="text-right tabular-nums">
                    {formatMoney(listing.latest?.price)}
                  </td>
                  <td>
                    {listing.latest ? (
                      <StockPill state={listing.latest.stock_state} />
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
