"""Evaluate ONE user-chosen set of outbound warehouses on the synthetic Nu-kote data.

Standalone add-on: it reuses load() and solve() from nukote_lp.py and changes no
existing file or output. It re-solves the LP for the warehouses you name and reports
  * total annual cost WITHOUT expansion fees (the LP cost), with its components
  * total annual cost WITH expansion fees
  * shipping-radius details (eligible lanes, nearest-warehouse exceptions,
    customers/units/distances per outbound warehouse)

Examples:
  python warehouse_config_cost.py W1 W2 W4
  python warehouse_config_cost.py T,C,P --radius 1500
  python warehouse_config_cost.py franklin connellsville --sweep
  python warehouse_config_cost.py            (asks for the warehouses interactively)

Warehouse names accepted (case-insensitive): W1/T/Franklin, W2/C/Chatsworth,
W3/N/Rochester, W4/P/Connellsville.
"""
import argparse
import sys

import numpy as np
import pandas as pd

import nukote_lp as model

FLOW_TOL = 1e-5   # same "meaningful flow" tolerance as nukote_lp.py
CITY = {'W1': 'Franklin TN', 'W2': 'Chatsworth CA', 'W3': 'Rochester NY', 'W4': 'Connellsville PA'}
ALIASES = {}
for w, tag in model.TAG.items():
    for alias in (w, tag, CITY[w].split()[0]):
        ALIASES[alias.lower()] = w


def parse_warehouses(tokens):
    """Turn ['W1,c', 'rochester'] into ['W1', 'W2', 'W3'] in W1..W4 order."""
    chosen = []
    for token in tokens:
        for part in token.replace(',', ' ').split():
            w = ALIASES.get(part.strip().lower())
            if w is None:
                raise ValueError(f'Unknown warehouse "{part}". Use W1-W4, T/C/N/P or a city name.')
            if w not in chosen:
                chosen.append(w)
    if not chosen:
        raise ValueError('Give at least one outbound warehouse.')
    return [w for w in model.WIDS if w in chosen]


def label(outbound):
    return ','.join(model.TAG[w] for w in outbound)


def money(x):
    return f'USD {x:>15,.2f}'


def radius_details(S, r):
    """Per-warehouse shipping facts for the solved configuration."""
    V = r['V']
    wc = V[(V.lane_type == 'W-C') & (V.flow > FLOW_TOL)]
    dist = S['distances'].query("lane_type == 'W-C'")
    rows = []
    for w in r['outbound']:
        mine = wc[wc.from_id == w]
        in_radius = int(((dist.from_id == w) & (dist.miles <= r['radius'])).sum())
        units = float(mine.flow.sum())
        rows.append(dict(
            warehouse=w, city=CITY[w],
            customers_within_radius=in_radius,
            customers_served=int(mine.to_id.nunique()),
            units_shipped=units,
            avg_miles_per_unit=float((mine.flow * mine.miles).sum() / units) if units else np.nan,
            max_miles_used=float(mine.miles.max()) if units else np.nan,
            outbound_freight_usd=float((mine.flow * mine.transport_cost).sum())))
    per_wh = pd.DataFrame(rows)
    served_by = wc.groupby('to_id').from_id.nunique()
    split = int((served_by > 1).sum())
    exc = pd.DataFrame(columns=['customer', 'name', 'region', 'assigned_warehouse', 'miles'])
    if r['exceptions']:
        loc = S['locations'].set_index('id')
        lanes = V[(V.lane_type == 'W-C') & V.to_id.isin(r['exceptions'])].drop_duplicates(['from_id', 'to_id'])
        exc = pd.DataFrame(dict(customer=lanes.to_id.values,
                                name=loc.loc[lanes.to_id, 'name'].values,
                                region=loc.loc[lanes.to_id, 'region'].values,
                                assigned_warehouse=lanes.from_id.values,
                                miles=lanes.miles.values)).sort_values('miles', ascending=False)
    return per_wh, exc, split


def report(S, r, baseline=None):
    out = r['outbound']
    fees = S['warehouse_expansion_costs'].set_index('warehouse_id').annual_fixed_cost_usd
    V = r['V']
    ww_transport = float((V.flow * V.transport_cost)[V.lane_type == 'W-W'].sum())
    out_transport = float((V.flow * V.transport_cost)[V.lane_type == 'W-C'].sum())

    print('=' * 72)
    print(f'Configuration {label(out)}  |  radius {r["radius"]:,.0f} miles  |  solver {r["backend"]}')
    print('=' * 72)
    print('Outbound warehouses and annual expansion fees:')
    for w in out:
        print(f'  {w} {CITY[w]:<18} {money(float(fees[w]))}')
    if 'W1' not in out:
        print('  NOTE: Franklin (W1) is not outbound. The paper/main study always keeps it;')
        print('        this run is outside the eight studied configurations.')

    print('\nANNUAL COST')
    print(f'  Inbound transport (vendor/plant -> warehouse) {money(r["inbound_transport"])}')
    print(f'  Inter-warehouse transport                     {money(ww_transport)}')
    print(f'  Outbound transport (warehouse -> customer)    {money(out_transport)}')
    print(f'  Holding (inventory) cost                      {money(r["holding"])}')
    print(f'  Handling cost                                 {money(r["handling"])}')
    print(f'  ---------------------------------------------------------------')
    print(f'  TOTAL EXCLUDING expansion cost (LP cost)      {money(r["objective"])}')
    print(f'  Expansion (fixed) cost                        {money(r["fixed_cost"])}')
    print(f'  TOTAL INCLUDING expansion cost                {money(r["total_cost"])}')
    if baseline is not None and baseline is not r:
        b = baseline['total_cost']
        print(f'  vs Franklin-only (W1) total {money(b)}: '
              f'{(b - r["total_cost"]) / b * 100:+.4f}% saving')

    per_wh, exc, split = radius_details(S, r)
    print(f'\nSHIPPING RADIUS DETAILS (radius = {r["radius"]:,.0f} miles)')
    print(f'  Eligible warehouse->customer lanes : {r["eligible_wc_arcs"]}')
    print(f'  Customers with no outbound warehouse inside the radius (exceptions): {len(r["exceptions"])}')
    print(f'  Customers served by more than one warehouse: {split}')
    print(f'  Inter-warehouse transfer volume: {r["ww_units"]:,.4f} units')
    table = per_wh.copy()
    table['units_shipped'] = table.units_shipped.map('{:,.0f}'.format)
    table['avg_miles_per_unit'] = table.avg_miles_per_unit.map('{:,.1f}'.format)
    table['max_miles_used'] = table.max_miles_used.map('{:,.1f}'.format)
    table['outbound_freight_usd'] = table.outbound_freight_usd.map('{:,.2f}'.format)
    print(table.to_string(index=False))
    if len(exc):
        print('\n  Exception customers (served by the NEAREST outbound warehouse, beyond the radius):')
        print(exc.to_string(index=False, formatters={'miles': '{:,.1f}'.format}))

    print('\nWAREHOUSE STORAGE USE (average pallet positions)')
    for w in model.WIDS:
        used, cap = r['pallets_used'][w], r['capacity'][w]
        role = 'outbound' if w in out else 'factory/inbound only'
        print(f'  {w} {CITY[w]:<18} {used:9.3f} / {cap:4d}  ({used / cap * 100:6.2f}%)  {role}')

    checks = r['validation']
    passed = sum(c['result'] == 'PASS' for c in checks)
    print(f'\nIndependent solution checks: {passed}/{len(checks)} PASS')


def sweep(S, outbound, radii, solver):
    rows = []
    for radius in radii:
        r = model.solve(S, outbound, radius, solver=solver)
        if r['status'] != 0:
            rows.append(dict(radius=radius, status='INFEASIBLE'))
            continue
        rows.append(dict(radius=radius, status='FEASIBLE', cost_excl_expansion=r['objective'],
                         expansion_cost=r['fixed_cost'], cost_incl_expansion=r['total_cost'],
                         eligible_wc_lanes=r['eligible_wc_arcs'], exceptions=len(r['exceptions'])))
    table = pd.DataFrame(rows)
    if 'cost_excl_expansion' in table:
        first = table.cost_excl_expansion.iloc[0]
        table['savings_vs_first_radius_pct'] = (first - table.cost_excl_expansion) / first * 100
    print('\nRADIUS SWEEP for configuration ' + label(outbound))
    print(table.to_string(index=False, float_format='{:,.2f}'.format))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('warehouses', nargs='*', help='outbound warehouses, e.g. W1 W2 W4 or T,C,P')
    p.add_argument('--radius', type=float, default=1000.0, help='shipping radius in miles (default 1000)')
    p.add_argument('--sweep', nargs='*', type=float, metavar='MILES',
                   help='also re-solve at these radii (default 1000 1500 2000 2500 3000)')
    p.add_argument('--data', default='nukote_synthetic_data_v2.xlsx', help='input workbook')
    p.add_argument('--solver', choices=['auto', 'highs', 'numpy'], default='auto')
    p.add_argument('--no-baseline', action='store_true', help='skip the Franklin-only comparison solve')
    args = p.parse_args(argv)

    tokens = args.warehouses
    if not tokens:
        tokens = [input('Outbound warehouses (e.g. W1 W2 W4, or T,C,P): ')]
    try:
        outbound = parse_warehouses(tokens)
    except ValueError as error:
        p.error(str(error))
    if args.radius < 0:
        p.error('radius must be nonnegative')

    S = model.load(args.data)
    r = model.solve(S, outbound, args.radius, solver=args.solver)
    if r['status'] != 0:
        print(f'Configuration {label(outbound)} at radius {args.radius:,.0f} is INFEASIBLE: {r["message"]}')
        # Every unit must be received at an outbound warehouse before delivery, so their
        # combined storage must hold at least the single-pass average inventory.
        caps = S['warehouse_capacity'].set_index('warehouse_id').capacity_pallets
        need = S['assumptions'].set_index('parameter').value.get('average_inventory_pallets')
        if need is not None:
            have = int(caps.reindex(outbound).sum())
            print(f'Outbound storage = {have} pallet positions; at least {float(need):.2f} are needed '
                  f'for all goods to pass through outbound warehouses.')
        return 1
    baseline = None
    if not args.no_baseline:
        baseline = r if outbound == ['W1'] else model.solve(S, ['W1'], args.radius, solver=args.solver)
        if baseline['status'] != 0:
            baseline = None
    report(S, r, baseline)
    if args.sweep is not None:
        sweep(S, outbound, args.sweep or [1000, 1500, 2000, 2500, 3000], args.solver)
    return 0


if __name__ == '__main__':
    sys.exit(main())
