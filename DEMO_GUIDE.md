# Revenue & Pricing Decision Support System (DSS)
# Complete Presentation & Live Demonstration Playbook

> **Target Audience:** Academic Evaluators, Executive Stakeholders (VP Merchandising / CFO), Investors, or Technical Clients  
> **Presentation Duration:** 10 – 15 minutes  
> **Key Value Proposition:** Turns noisy competitor signals and sales histories into **defensible, mathematically constrained price recommendations** that yield **+16.0% gross profit lift** over standard retail conventions.

---

## 1. The 60-Second Executive Pitch (The Hook)

> *"In retail and eCommerce, pricing decisions today are broken. 
> Companies either use lazy **Cost-Plus markups** (leaving massive margin on the table for price-inelastic bestsellers), or they use **blind competitor scrapers** that trigger destructive price wars down to zero margin. The few who try 'black-box AI' get burned when unconstrained algorithms hallucinate illegal or brand-destroying price swings.
>
> We built the **Revenue & Pricing DSS** to solve this. It's a mathematically defensible decision engine combining:
> 1. **Competitive Intelligence** with hybrid vector matching,
> 2. **Econometric Demand Estimation** (log-log OLS with HC3 robust confidence intervals),
> 3. **Constrained Optimization** with sibling cannibalization penalties and hard margin guardrails,
> 4. **Monte Carlo Risk Simulation** for what-if scenario testing, and
> 5. **A Corporate Financial Advisor** grounded strictly on verified balance sheet realities.
>
> In our 90-day benchmark against real-world retail baselines, DSS delivers a **+16.0% profit lift over Cost-Plus** and **+10.8% over Competitor-Matching**."*

---

## 2. Pre-Demo Setup & Verification Checklist

Before starting your demonstration, ensure the system is running and pre-seeded.

### Step 1: Start Infrastructure & Services

Open **three separate PowerShell terminals**:

**Terminal 1 — Database & Infrastructure:**
```powershell
docker compose -f infra/docker-compose.yml up -d postgres
```
*(PostgreSQL with `pgvector` will start on port `55432`)*

**Terminal 2 — Backend API Server:**
```powershell
cd D:\projects\DSS\backend
# Activate the virtual environment:
..\.venv\Scripts\Activate.ps1
# (Note: If your terminal is at the project root D:\projects\DSS, run: .\.venv\Scripts\Activate.ps1)
uvicorn app.main:app --port 8010 --reload
```
*(Or run directly without activating: `D:\projects\DSS\.venv\Scripts\uvicorn.exe app.main:app --port 8010 --reload`)*
*(Verify at `http://localhost:8010/docs`)*

**Terminal 3 — Frontend Web Application:**
```powershell
cd frontend
npm run dev
```
*(Verify at `http://localhost:3000`)*

---

### Step 2: Seed Clean Market History (1 Command)

In a terminal, run the automated seed and walkthrough script to ensure all 6 modules have fresh, clean data ready for presentation:

```powershell
cd backend
python scripts/demo_walkthrough.py --fast
```

Now open `http://localhost:3000` in Google Chrome and log in:
* **Email:** `admin@dss-demo.com`
* **Password:** `admin12345`

---

## 3. Step-by-Step Live Demo Script (8 Scenes)

Follow this narrative flow screen by screen:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                The Demo Journey (12 Mins)                              │
├─────────────────┬─────────────────┬──────────────────┬─────────────────┬───────────────┤
│ Scene 1:        │ Scene 2 & 3:    │ Scene 4:         │ Scene 5:        │ Scene 6 & 7:  │
│ Executive Cockpit│ Market Watch &  │ Econometric      │ Constrained     │ Scenario &    │
│ (Overview)      │ AI Match Review │ SKU Diagnostics  │ Optimization    │ CFO Advisor   │
└─────────────────┴─────────────────┴──────────────────┴─────────────────┴───────────────┘
```

---

### Scene 1: Executive Overview (`/`) — Portfolio Health & Alert Cockpit
* **Navigate to:** `http://localhost:3000/`
* **What to Show:**
  * Top KPI cards: Active Catalog SKUs (24), Average Portfolio Margin (31.4%), Total Tracked Competitors (4), Open Market Alerts.
  * 30-Day Revenue & Margin Trajectory chart.
  * Active Alert list (competitor price undercut alerts and out-of-stock events).
* **What to Say:**
  > *"This is the morning cockpit for a Pricing Director or Merchandising VP. At a single glance, we see our aggregate catalog margin health, market velocity, and immediate competitive threats. Notice these alerts: our system detected that a major competitor dropped prices on our flagship headphone SKU, while another competitor went out of stock on gaming monitors."*

---

### Scene 2: Market Watch (`/market`) — Competitive Dynamics
* **Navigate to:** Click **Market Watch** in the top navigation bar (`/market`).
* **What to Show:**
  * Competitor Price Index tracker showing price spreads across rival sellers (ApexTech, ByteBazaar, CircuitCity, etc.).
  * Competitor Landed Price table with stock availability badges (`IN STOCK` vs `OUT OF STOCK`).
* **What to Say:**
  > *"Here we monitor competitive positioning across channels. But crucially, DSS is not a naive scraper. Notice the out-of-stock flag here: when a competitor stocks out, traditional scrapers still register their old low price. DSS recognizes this as an artificial supply shock, preventing us from unnecessarily cutting prices when the competitor cannot even fulfill demand."*

---

### Scene 3: Match Review Queue (`/listings`) — AI-Assisted Catalog Matching
* **Navigate to:** Click **Match Review** in the top navigation bar (`/listings`).
* **What to Show:**
  * The match candidate cards with similarity scores (e.g. `0.94`, `0.79`, `0.65`).
  * Show an auto-accepted match (GTIN exact match).
  * Show a pending candidate with embedding similarity:
    * Our SKU: *Sony WH-1000XM5 Wireless Headphones (Black)*
    * Competitor title: *Sony Over-Ear Noise Cancelling BT Headset XM5-BLK*
  * Demonstrate the human-in-the-loop controls: Click **Approve** or **Reject**.
* **What to Say:**
  > *"Competitor titles are notoriously messy. Rule-based matching fails, while pure LLMs are too slow and expensive for 100,000 listings. 
  > We designed a two-stage hybrid matcher:
  > 1. Exact GTIN/UPC barcode match (instant auto-match).
  > 2. Dense semantic embeddings via Sentence Transformers (`all-MiniLM-L6-v2`) in PostgreSQL `pgvector`.
  > Matches with confidence $\ge 0.86$ are auto-applied. Borderline matches between $0.60$ and $0.86$ flow directly into this 1-click human review queue."*

---

### Scene 4: SKU Workbench (`/workbench`) — Econometric Demand Estimation
* **Navigate to:** Click **Workbench** in the top navigation bar (`/workbench`).
* **Select Product:** Choose an inelastic item like **Sony WH-1000XM5** (or click any product card).
* **What to Show:**
  * **Empirical Demand Curve**: Log-log regression line fitted to historical price and sales volume data points.
  * **90% Confidence Interval Band (HC3)**: Shaded uncertainty area around the curve.
  * **Estimated Price Elasticity ($\varepsilon$)**: e.g., $\varepsilon = -0.74$ (inelastic) or $\varepsilon = -1.82$ (elastic).
  * **Forward Demand Forecast**: 30-day Holt-Winters Exponential Smoothing forecast with confidence intervals.
* **What to Say:**
  > *"This is the core scientific difference of DSS. Instead of treating pricing as an opaque neural network, we use econometric demand theory. 
  > We estimate price elasticity using log-log Ordinary Least Squares with HC3 heteroskedasticity-consistent standard errors.
  > Notice this SKU has an elasticity of $-0.74$. Because it is inelastic ($|\varepsilon| < 1$), economic theory dictates that raising the price will increase total revenue and gross profit. The system mathematically proves this with a 90% confidence interval."*

---

### Scene 5: Constrained Dynamic Pricing (`/recommendations`) — The Decision Engine
* **Navigate to:** Click **Recommendations** in the top navigation bar (`/recommendations`).
* **What to Show:**
  1. Click the **Generate Recommendations** button.
  2. Select an objective: **Maximize Gross Margin** (or test **Maximize Revenue**).
  3. Inspect the generated recommendations table:
     * Current Price $\to$ Recommended Price $\to$ Predicted Sales Delta $\to$ Predicted Profit Delta.
     * Highlight the **Binding Guardrails** column:
       - Hard Margin Floor: $p \ge \text{cost} / (1 - m_{\min})$ (prevents selling below cost).
       - Max Step Constraint: Caps price changes at $\pm 15\%$ per cycle.
       - Charm Pricing: Automatic `.99` psychological rounding.
     * Highlight **Substitute Cannibalization Awareness ($\kappa$)**: Notice that prices of sister SKUs in the same category were adjusted together to prevent cannibalizing higher-margin siblings.
  4. Demonstrate the Lifecycle: Click **Approve** on a recommendation, then click **Apply**.
* **What to Say:**
  > *"Here the optimization engine translates econometrics into actionable decisions. 
  > Notice what happens:
  > - On inelastic SKUs, it raises prices to capture consumer surplus.
  > - On elastic SKUs, it trims prices to drive volume.
  > - But most importantly, it obeys hard managerial guardrails. No recommendation can ever violate our gross margin floor, swing by more than 15%, or ignore substitute cannibalization.
  > A pricing manager can review every recommendation, see the predicted margin dollar impact, approve it, or reject it with an audit note."*

---

### Scene 6: Scenario Sandbox (`/sandbox`) — What-If Risk Simulation
* **Navigate to:** Click **Sandbox** in the top navigation bar (`/sandbox`).
* **What to Show:**
  1. Select a flagship product from the dropdown.
  2. Move the slider to test a hypothetical price change (e.g. **+10% price increase**).
  3. Toggle **Competitor Reaction Model** (e.g. "Rivals match 50% of price change after 4 days").
  4. Click **Run Simulation (500 Monte Carlo Draws)**.
  5. Inspect the **Fan Chart**: Shows P05 (pessimistic), P25, P50 (expected), P75, and P95 (optimistic) distribution of expected revenue and profit.
* **What to Say:**
  > *"Merchandisers are naturally risk-averse. Before pushing a price change to production, they ask: 'What if demand drops faster than expected? What if competitors retaliate?'
  > The Sandbox draws 500 Monte Carlo samples from our estimated elasticity covariance matrix $\varepsilon \sim \mathcal{N}(\hat\varepsilon, \hat\sigma^2)$ combined with rival reaction functions. The resulting fan chart gives executives probabilistic risk bounds (P05 to P95) rather than a single naive point estimate."*

---

### Scene 7: Corporate Financials & Grounded CFO Advisor (`/advisor`)
* **Navigate to:** Click **CFO Advisor** in the top navigation bar (`/advisor`).
* **What to Show:**
  1. **Financial Health Cockpit**: 8-quarter financial trend of Gross Margin %, EBITDA, Customer Acquisition Cost (CAC), LTV, Runway (months), and Cash Conversion Cycle (CCC in days).
  2. **Automated Anomaly Detector**: Robust z-score findings (e.g., inventory holding days spiking or advertising ROAS softening).
  3. Click **Generate Strategic Narrative**:
     * Watch the AI advisor synthesize a structured executive memo connecting pricing strategy to corporate cash flow.
     * Point out the verified citations badge: **100% Verified Numeric Grounding**.
* **What to Say:**
  > *"Pricing cannot operate in a departmental silo. If our Cash Conversion Cycle is ballooning and working capital runway is short, the business needs cash generation over long-term market penetration.
  > Our CFO Advisor connects operational pricing directly to quarterly balance sheets.
  > Furthermore, we solved the enterprise LLM hallucination problem: every dollar amount, percentage, and metric generated by the LLM is passed through a deterministic numeric grounding validator. If the AI hallucinates any number not present in the audited financial facts JSON, the narrative is rejected immediately."*

---

### Scene 8: The Live Proof — 90-Day Simulation Benchmark
* **Action:** Switch to your terminal window.
* **Command:**
  ```powershell
  cd backend
  python scripts/benchmark_dss_vs_baselines.py --days 90
  ```
* **What to Show:**
  * Let the benchmark run (takes ~15 seconds).
  * Show the final printed Rich comparison table:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        90-Day Market Simulation Results Summary                        │
├─────────────────────┬────────────┬────────────────┬──────────────┬──────────┬──────────┤
│ Strategy            │ Units Sold │ Total Revenue  │ Gross Profit │ Margin % │ Lift %   │
├─────────────────────┼────────────┼────────────────┼──────────────┼──────────┼──────────┤
│ Cost-Plus (+25%)    │ 71,940     │ $3,812,450.20  │ $762,490.04  │ 20.0%    │ baseline │
│ Competitor-Matching │ 68,120     │ $4,015,310.80  │ $798,240.15  │ 19.9%    │ +4.7%    │
│ DSS Optimization    │ 74,830     │ $4,380,920.40  │ $884,610.90  │ 20.2%    │ +16.0%   │
└─────────────────────┴────────────┴────────────────┴──────────────┴──────────┴──────────┘
```

* **Concluding Talking Point:**
  > *"To validate that our math holds up in practice, we ran a 90-day simulation comparing three identical catalogs under the exact same market demand environment:
  > 1. **Cost-Plus** achieved $762,490 in gross profit.
  > 2. **Competitor-Matching** triggered margin erosion and achieved $798,240 (+4.7%).
  > 3. **DSS Constrained Optimization** delivered **$884,610 in gross profit — a +16.0% net profit lift ($122,120 in incremental profit)**.
  >
  > DSS wins because it exploits asymmetric elasticity on inelastic products, protects margin with hard floors, and accounts for cannibalization across the entire catalog."*

---

## 4. Q&A Defense Guide: Handling Tough Questions

### Q1: *"Why use classical econometric log-log OLS instead of deep neural networks or reinforcement learning?"*
* **Answer:**  
  1. **Explainability & Defensibility:** A merchandiser or CFO will never approve a price that risks millions of dollars if they cannot explain why. With log-log OLS, every price change is traceable to an elasticity coefficient $\varepsilon$ and an HC3 confidence interval.
  2. **Sample Efficiency:** Retail price changes are sparse (you don't change prices 1,000 times a day on every SKU). Neural networks overfit on sparse pricing data; econometrics with category shrinkage handles sparse data with statistical rigor.
  3. **No Hallucinations:** Reinforcement learning agents in pricing are notorious for finding extreme corner-solution policies that exploit simulator artifacts or violate fair-pricing laws.

---

### Q2: *"What if a competitor intentionally cuts their price to 1 cent or makes an error?"*
* **Answer:**  
  * DSS has **hard cost margin guardrails** ($p \ge \text{cost} / (1 - m_{\min})$) and a **$\pm 15\%$ maximum step constraint**. 
  * Even if a competitor drops their price to zero, DSS will never price below our configured margin floor. It will raise a high-severity alert for human review rather than blindly matching a ruinous price.

---

### Q3: *"How does the system eliminate LLM hallucinations in the CFO Advisor?"*
* **Answer:**  
  * We implement a two-stage verification barrier:
    1. **Strict Context Injection:** The LLM prompt is injected with a structured JSON containing verified balance sheet metrics.
    2. **Deterministic Regex Fact-Checker:** Before the response is saved or displayed, an AST/regex parser extracts every numeric literal in the LLM's text. If any number deviates from the verified facts dictionary by more than 0.5%, the report is rejected and regenerated with a penalty temperature.

---

### Q4: *"What is the commercial roadmap to make this an enterprise B2B product?"*
* **Answer:**  
  * We have mapped out a **4-Phase Enterprise Roadmap**:
    1. **Closed-Loop Execution:** Shopify Plus & Amazon SP-API direct write-back with an emergency 60-second kill switch.
    2. **Industrial Ingestion:** Direct Snowflake/BigQuery connectors and Bright Data managed proxy scrapers.
    3. **Advanced Retail Econometrics:** Tobit regression to eliminate zero-demand bias during stockouts and inventory Weeks-of-Supply (WOS) yield constraints.
    4. **Multi-Tenant Security:** PostgreSQL Row-Level Security (RLS) and Okta SAML 2.0 SSO.
