# Nu-kote synthetic LP: audit and validation

## Scope and source evidence

LeBlanc et al., *Interfaces* 34(2), 139-146 (2004), DOI
10.1287/inte.1030.0051, was read from the original local PDF. Original PDF and v1
files remain unchanged; timestamped backups preserve every pre-cleanup deliverable.

**Paper facts:** 5 vendors, 5 plants, 4 warehouses, 394 aggregate customers,
6 product groups; 1.14 circuit factor; product-specific source upper bounds,
warehouse/product conservation, exact demand, shared pallet capacity; holding and
handling on every warehouse receipt, including transfers (pp. 141, 145-146).
All eight outbound subsets retaining Franklin are compared; other warehouses
remain usable as factory warehouses. Expansion fees are exogenous annual costs
(p. 143). Figure 3 reports nearly 18% lower LP costs when the radius expands.
The historical freight observations and inventory conversion parameters are not
provided. Historical 5% cost agreement and Excel Solver have **not** been reproduced.

## Findings and justified corrections

The old generator calibrated fees using LP savings to make Chatsworth win,
forced western sourcing, discounted interwarehouse freight by 50%, and chose
capacities to induce desired behavior. Those outcome-based choices and checks
were removed. The generator now invokes no optimizer. No parameter was tuned
to reproduce either the preferred historical configuration or 18% savings.

Original radius eligibility **did change**, from 394 to 788 outbound lanes.
Original LP savings were 0.507%; holding/handling comprised 51.3% of baseline LP
cost and transport fell only about 0.1765%. Approximately 72,888 units of transfers
were eliminated, reducing repeated inventory/handling charges. Cheap inbound TL
plus local delivery often dominates a newly permitted long LTL leg. Increasing
eligibility therefore need not materially reduce cost. The old figures also used
truncated cost axes. Figure 2 now stacks the four computed components on a zero-based
annual-dollar scale, marks the baseline and highlights the computed minimum.
Figure 4 uses a disclosed truncated normalized-cost axis and a zero-based savings axis.

## Synthetic assumptions and formulation

Seed is 42. Demand, supplier eligibility, weights, packaging, freight noise/rates,
fees, inventory cycles and capacities are synthetic. City names and network counts
follow the paper where given; vendor/customer coordinates are synthetic geographic
proxies. No historical freight regression was fabricated.

Each source serves 1-4 products (plants favor 1-2); every product has at least two
sources. Product capacity is independently 1.15-1.35 times demand, split randomly,
without a geographic quota. Capacities are upper bounds: cheaper sources may be
chosen while dearer capacity remains unused. There are no source purchase costs,
so this is logistics-cost sourcing among fungible product categories.

Flows x are units/year; freight is USD/unit = rate USD/(lb mile) * miles * lb/unit,
with fixed mean-one lognormal lane/product noise (sigma 0.07). Overseas origins add
a per-lb ocean surcharge. Transfers use the same TL base as other inbound lanes.
Base nominal LTL/TL ratio is 1.832670. Independent W2-W4 annual expansion fees
are USD 150,000-450,000 rounded to USD 1,000; W1 has zero incremental fee.

The **synthetic 15-day replenishment cycle** gives average stock = annual receipts
* 15/730. Holding per annual unit = cost_of_capital * standard_cost * 15/730;
physical pallet positions per annual unit a[p] = 15/(730 * units_per_pallet[p]).
Using the same fraction for holding and storage fixes the stock/throughput unit
mismatch. The paper states annual r*c per inventory unit but does not specify this
15-day conversion. Single-pass annual throughput is 3,815.0881 pallet equivalents,
whereas average inventory is 78.3922 physical pallet positions. Units per
pallet imply about 1,955-2,482 lb gross loads here; cube and safety stock are unknown.
Storage is independently drawn at 1.10-1.30 times this inventory for W1 and
0.30-0.70 for others, rounded upward; it is not an annual throughput limit.

```text
min  sum(a,p) c[a,p]*x[a,p] + sum(w) F[w]*z[w]
source/product:     sum(outgoing x) <= source capacity
warehouse/product:  sum(incoming x) = sum(outgoing x)
customer/product:   sum(eligible warehouse x) = annual demand
warehouse storage:  sum(incoming a,p) a[p]*x[a,p] <= pallet positions
x >= 0; z is fixed by enumerated configuration
```

c includes transport plus holding and destination handling on V-W, P-W and W-W;
W-C has only transport. All 12 ordered W-W pairs are available; no self-loops.
A further warehouse visit incurs another stock, holding and handling charge.
Each selected outbound warehouse is charged its fee exactly once, even if unused.

W-C requires an active outbound warehouse within R. If none exists, permit only
the **nearest active outbound warehouse**, with ties in W1,W2,W3,W4 order. The
paper mentions exceptions but gives no assignment algorithm; this nearest-only
rule is a disclosed implementation assumption. Exceptions are recomputed per
configuration/radius. It guarantees an arc, not capacity feasibility in arbitrary
networks; actual feasibility is tested here. Radius arc sets must be nested and
LP costs nonincreasing. Zero-demand rows are retained in the input and formulation.

## Executed solver validation

Backend actually used: **NumPy Mehrotra primal-dual LP**. SciPy/HiGHS remains preferred when installed.
The existing NumPy Mehrotra fallback is retained; it eliminates the diagonal demand
block, solves a core system of at most 88 rows, requires primal/dual convergence
and a sub-cent gap, and recovers dual multipliers for eliminated zero-demand/source
variables. Infeasibility requires an empty-equality or Farkas certificate.

Fresh HiGHS vs NumPy comparison: SKIP. SciPy/HiGHS is not installed in the execution environment and package installation was unavailable. No fresh HiGHS solve or independent cross-solver agreement is claimed. The NumPy fallback and its analytical/primal-dual tests were executed.

Independent checks rebuild every feasible solution from flow records and raw
inputs, without LP constraint matrices: exact customer demand, source limits,
warehouse/product balance, storage, allowed routes/exceptions, one fixed charge,
reconstructed cost, dual feasibility and primal-dual objective agreement. Tolerances:
1e-5 units/pallets; objective USD 0.01 + 1e-9*abs(LP objective). Genuine failures abort.

Analytical tests: 10-unit demand with W1 storage 6 forces 4 units through W2,
known LP $18 and fixed-inclusive total $23; cheaper supply is used with capacity
left unused. With storage 10 and radius 50, the nearest-warehouse exception gives
$10. A two-product test with pallet conversions 1 and 0.5 gives $38. An intentionally
infeasible capacity case is certified. Deliberately corrupted demand/flow, source
capacity, storage, fixed fee, objective and prohibited route are all rejected.

**2262 checks: 2261 PASS,
0 FAIL,
0 feasibility WARN,
1 SKIP.** Input generation
has a separate 89-check suite. All eight archived v2 objectives were reproduced within USD 0.000004593. This is agreement with saved outputs, not a fresh independent HiGHS execution. The original 0.5070167% radius saving was also reproduced.

## Current results

All eight configurations are feasible. **Best: T,C,P**
(T=Franklin, C=Chatsworth, N=Rochester, P=Connellsville),
**USD 3,244,587.75/year**, saving 10.2251% versus W1 only.
The workbook gives transportation, holding, handling, fees and utilization separately.

| config | overall_cost | fixed_cost | rel_overall | savings_pct |
| --- | --- | --- | --- | --- |
| T | 3,614,135.905200 | 0.000000 | 1.000000 | 0.000000 |
| T,C | 3,349,134.381000 | 190,000.000000 | 0.926676 | 7.332362 |
| T,N | 3,605,004.868300 | 318,000.000000 | 0.997474 | 0.252648 |
| T,P | 3,507,578.898168 | 270,000.000000 | 0.970517 | 2.948340 |
| T,C,N | 3,340,937.947001 | 508,000.000000 | 0.924408 | 7.559150 |
| T,C,P | 3,244,587.745052 | 460,000.000000 | 0.897749 | 10.225076 |
| T,N,P | 3,769,459.003502 | 588,000.000000 | 1.042977 | -4.297655 |
| T,C,N,P | 3,508,216.803900 | 778,000.000000 | 0.970693 | 2.930689 |

## Why transfers vanish and capacity binds at Connellsville

The formulation permits all 12 W-W lanes; zero transfer flow is not an eligibility
bug. All tested eligible source/product two-leg detours are strictly more costly than the direct inbound lane. Among 324 eligible source/product detours, the minimum
extra receipt-plus-freight cost is USD 0.043600/unit.
Direct bypass also removes the intermediate warehouse's holding, handling and
storage use. Source/product output and the final warehouse's receipts stay the same.
No consolidation discount, manufacturing-input dependency or mandatory factory
receipt is imposed, so an intermediate stop need not have any economic purpose.
Fixing all W-W flows to zero and re-solving all eight configurations changed the
objective by at most USD 0.000004427.
Raw numerical transfer residuals are retained, not represented as physical shipments;
meaningful flow is defined using the documented 1e-5-unit tolerance.

Franklin-only utilization is 83.396% because synthetic
Franklin capacity was deliberately drawn with spare stock capacity, independently
of solutions. In the best configuration W1 utilization is 34.967%
and W4 is 100.000%. The eastern customer geography, W4's
lower handling cost and its independently drawn 34 pallet positions make W4's
capacity valuable. Adding 0.01 position at W4 reduces LP cost by USD 32.657669
(dual prediction USD 32.657669); the same increase at W1
saves USD -0.000000022 (numerical zero within the USD 0.01 tolerance).
These are diagnostic perturbations only; original
capacities and final results were retained. The paper uses different confidential
inputs, so the warehouse that binds need not be the same. Detailed evidence is in
network_diagnostics, transfer_dominance and capacity_perturbations.

Franklin + Chatsworth radius sweep; LP normalization excludes the constant fixed
fee. Actual fixed-inclusive dollars and their own normalization are also exported.

| radius | lp_cost | normalized_cost | savings_pct | eligible_wc_arcs | exceptions |
| --- | --- | --- | --- | --- | --- |
| 1000 | 3,159,134.381000 | 1.000000 | 0.000000 | 394 | 15 |
| 1500 | 3,130,371.425800 | 0.990895 | 0.910470 | 430 | 0 |
| 2000 | 3,129,761.637500 | 0.990702 | 0.929772 | 454 | 0 |
| 2500 | 3,129,761.637500 | 0.990702 | 0.929772 | 661 | 0 |
| 3000 | 3,129,761.637500 | 0.990702 | 0.929772 | 788 | 0 |

Savings are **0.929772%**, leaving a
**17.070228 percentage-point** gap to the approximate paper
benchmark. The curve flattens once added arcs are economically unattractive.
No claim is made to reproduce the confidential historical network.
The supplied PDF's Figure 3 (p. 145) was visually inspected. The reference values
1.00, 0.90, 0.82, 0.82, 0.82 at the five radii are consistent approximate visual
readings, not exact published observations. They are exported in paper_reference
and plotted as five distinct points, including the plateau after 2,000 miles.

## Controlled sensitivities

For each nominal LTL/TL ratio, all five radii were solved with all other inputs
fixed. Only outbound linehaul changes; TL, ocean, noise, storage and other costs
stay fixed. Each curve uses its own 1,000-mile LP baseline; actual dollars are
exported. Endpoints at 3,000 miles:

| rate_ratio | corrected_savings_pct | original_v2_savings_pct |
| --- | --- | --- |
| 1.000000 | 4.031317 | 0.814580 |
| 1.250000 | 1.904604 | 0.709011 |
| 1.500000 | 1.213616 | 0.609985 |
| 1.750000 | 0.992112 | 0.516604 |
| 2.000000 | 0.813085 | 0.428212 |
| 2.250000 | 0.648555 | 0.344496 |
| 2.500000 | 0.499112 | 0.265054 |

Higher LTL/TL makes long customer legs less attractive, flattening the curves.
On the untouched original inputs even ratio 1 yields only about 0.815%, so the
ratio alone cannot explain the paper's nearly 18%. Synthetic product mix, source
availability, spatial demand and inventory/capacity assumptions differ. Both the
original and corrected controlled experiments are in the workbook. Figure 4 contains
only current synthetic inputs; archived synthetic comparisons are in appendix
Figure A1 and are not described as historical validation. All four archived source
files are included under backups/ with relative paths and SHA-256 in archive_provenance.

All eight configurations were re-solved at every ratio (56 LPs). Preferred choices:

| rate_ratio | config | overall_cost |
| --- | --- | --- |
| 1.000000 | T,C,P | 3,054,635.471848 |
| 1.250000 | T,C,P | 3,112,865.755233 |
| 1.500000 | T,C,P | 3,169,786.700389 |
| 1.750000 | T,C,P | 3,226,026.308539 |
| 2.000000 | T,C,P | 3,282,129.024181 |
| 2.250000 | T,C,P | 3,338,032.649969 |
| 2.500000 | T,C,P | 3,393,690.554611 |

Fees were multiplied uniformly; flows need not be re-solved because a fixed fee
is constant within a configuration. Preferred choices:

| fixed_cost_multiplier | config | overall_cost |
| --- | --- | --- |
| 0.000000 | T,C,N,P | 2,730,216.803900 |
| 0.500000 | T,C,P | 3,014,587.745052 |
| 1.000000 | T,C,P | 3,244,587.745052 |
| 1.500000 | T,C | 3,444,134.381000 |
| 2.000000 | T,C | 3,539,134.381000 |

## Reproduction, artifacts and limits

From the project directory (NumPy, pandas, openpyxl, matplotlib; SciPy optional):

```powershell
python -m pip install -r requirements.txt
# Recommended primary backend, when available: python -m pip install scipy
python generate_nukote_data_v2.py
python nukote_lp.py nukote_synthetic_data_v2.xlsx .
python nukote_lp.py --self-test
python validate_submission.py
```

Both scripts now use normal imports without machine-specific search paths.
The executed commands used the project-local .venv (see README.md); the existing
pure-Python Excel dependency was placed in that environment, not imported via a
script-specific path hack. Every cell of every sheet is compared against a fresh
seed-42 regeneration during the model run; exact workbook ZIP bytes need not match
because XLSX metadata can vary. Workbooks
include raw inputs, assumptions, configuration/radius/rate/fee results, full best
flows, utilization, duals, per-solution validation, advisory warnings, versions and
SHA-256 hashes. Excel exports are reopened and compared before plotting; figures
consume those saved values. Each PNG embeds the run ID and final workbook SHA-256
for automatic same-run checks. Degenerate flows may differ by solver version.

Limitations: divisible flow, linear freight, approximate distances, cycle stock
without safety stock/peaks/cube, no dock/throughput constraints, seasonality,
reverse logistics or transit-time model. Original freight observations, demand,
costs and inventory policies are needed for historical validation or replication.
Neither historical 5% agreement nor Excel Solver has been reproduced.
