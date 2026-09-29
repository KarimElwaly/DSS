"use client";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8010";

const TOKEN_KEY = "dss.token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError("Session expired. Please sign in again.", 401);
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }

  return (await response.json()) as T;
}

export async function login(email: string, password: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new ApiError("Invalid email or password.", response.status);
  }
  const data = (await response.json()) as { access_token: string };
  setToken(data.access_token);
}

// ---------------------------------------------------------------- types

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "pricing_manager" | "analyst";
  organization_id: string;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface Category {
  id: string;
  name: string;
  slug: string;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  brand: string;
  gtin: string | null;
  status: string;
  current_price: string;
  unit_cost: string;
  msrp: string | null;
  min_margin_pct: string;
  category: Category | null;
}

export interface MarketPosition {
  cheapest_competitor: string | null;
  cheapest_competitor_price: number | null;
  competitor_price_index: number | null;
  price_gap_pct: number | null;
  n_listings: number;
  n_out_of_stock: number;
  observed_at: string | null;
}

export interface ProductDetail extends Product {
  description: string;
  map_price: string | null;
  max_price_step_pct: string;
  launch_date: string | null;
  market: MarketPosition | null;
}

export interface SalesPoint {
  sale_date: string;
  units: number;
  unit_price: number;
  unit_cost: number;
  promo_flag: boolean;
  revenue: number;
  gross_margin: number;
}

export interface CompetitorPricePoint {
  competitor_slug: string;
  competitor_name: string;
  observed_at: string;
  price: number;
  landed_price: number;
  stock_state: string;
  promo_flag: boolean;
  url: string;
}

export interface MarketSnapshot {
  product_id: string;
  sku: string;
  name: string;
  our_price: number;
  competitor_price_index: number | null;
  cheapest_competitor_price: number | null;
  price_gap_pct: number | null;
  n_listings: number;
  n_out_of_stock: number;
  points: CompetitorPricePoint[];
}

export interface Listing {
  id: string;
  competitor_id: string;
  competitor_name: string | null;
  external_id: string;
  title: string;
  brand: string;
  gtin: string | null;
  url: string;
  matched_product_id: string | null;
  matched_product_sku: string | null;
  match_confidence: number | null;
  match_method: string;
  match_status: string;
  latest: {
    price: number;
    shipping_cost: number;
    landed_price: number;
    stock_state: string;
    promo_flag: boolean;
    observed_at: string;
  } | null;
}

export interface ElasticityEstimate {
  id: string;
  product_id: string;
  sku: string;
  product_name: string;
  category_name: string | null;
  method: string;
  elasticity: number;
  std_error: number | null;
  ci_low: number | null;
  ci_high: number | null;
  p_value: number | null;
  r_squared: number | null;
  n_observations: number;
  price_variation_cv: number | null;
  is_reliable: boolean;
  current_price: number;
  reference_price: number | null;
  diagnostics: Record<string, unknown>;
}

export interface DemandCurvePoint {
  price: number;
  units: number;
  lower_units: number;
  upper_units: number;
  revenue: number;
  margin: number;
}

export interface CrossElasticity {
  id: string;
  related_product_id: string;
  related_sku: string;
  related_name: string;
  elasticity: number;
  std_error: number | null;
  p_value: number | null;
  is_significant: boolean;
}

export interface SkuWorkbench {
  product_id: string;
  sku: string;
  name: string;
  brand: string;
  category_name: string | null;
  current_price: number;
  unit_cost: number;
  min_margin_pct: number;
  estimate: ElasticityEstimate | null;
  demand_curve: DemandCurvePoint[];
  cross_elasticities: CrossElasticity[];
}

export interface ForecastPoint {
  target_date: string;
  predicted_units: number;
  lower_units: number | null;
  upper_units: number | null;
  assumed_price: number | null;
}

export interface SkuForecast {
  product_id: string;
  sku: string;
  model_run_id: string;
  horizon_days: number;
  points: ForecastPoint[];
  backtest_wape: number | null;
}

export interface FitReport {
  model_run_id: string;
  label: string;
  created_at: string;
  n_products: number;
  metrics: Record<string, unknown>;
}

export interface PriceRecommendation {
  id: string;
  product_id: string;
  sku: string;
  product_name: string;
  category_name: string | null;
  objective: "margin" | "revenue" | "penetration";
  current_price: number;
  recommended_price: number;
  unconstrained_price: number | null;
  price_floor: number;
  price_ceiling: number;
  expected_units: number | null;
  expected_revenue: number | null;
  expected_margin: number | null;
  baseline_revenue: number | null;
  baseline_margin: number | null;
  elasticity_used: number | null;
  confidence: number | null;
  binding_constraints: string[];
  rationale: Record<string, unknown>;
  explanation: string;
  status: "proposed" | "approved" | "rejected" | "applied" | "expired";
  created_at: string;
  decided_at: string | null;
  decision_note: string;
}

export interface SimulationQuantiles {
  revenue: { p05: number; p25: number; p50: number; p75: number; p95: number };
  margin: { p05: number; p25: number; p50: number; p75: number; p95: number };
  units: { p05: number; p50: number; p95: number };
}

export interface SimulationSkuImpact {
  product_id: string;
  sku: string;
  name: string;
  current_price: number;
  new_price: number;
  price_delta_pct: number;
  units_delta_pct: number;
  expected_revenue: number;
  expected_margin: number;
}

export interface SimulationRun {
  id: string;
  name: string;
  created_at: string;
  horizon_days: number;
  scenario: {
    price_overrides_count: number;
    competitor_reaction_pct: number;
    horizon_days: number;
    overrides: Record<string, number>;
  };
  results: {
    baseline: { revenue: number; margin: number; units: number; margin_rate: number };
    simulated: { revenue: number; margin: number; units: number; margin_rate: number };
    delta: {
      revenue_abs: number;
      revenue_pct: number;
      margin_abs: number;
      margin_pct: number;
      units_abs: number;
      units_pct: number;
    };
    quantiles: SimulationQuantiles;
    sku_impacts: SimulationSkuImpact[];
  };
  n_monte_carlo: number;
}

export interface FinancialPeriod {
  id: string;
  label: string;
  granularity: string;
  period_start: string;
  period_end: string;
  currency: string;
  metrics: Record<string, number>;
}

export interface FinancialFinding {
  id: string;
  period_id: string;
  period_label: string;
  metric_key: string;
  severity: "info" | "warning" | "critical";
  title: string;
  detail: string;
  current_value: number | null;
  expected_value: number | null;
  delta_pct: number | null;
  z_score: number | null;
}

export interface AdvisorReport {
  id: string;
  period_id: string | null;
  model_name: string;
  facts: Record<string, unknown>;
  narrative: string;
  recommendations: Array<{ priority?: string; category?: string; action: string; expected_impact?: string }>;
  grounding_passed: boolean;
  grounding_violations: string[];
  latency_ms: number | null;
  created_at: string;
}




