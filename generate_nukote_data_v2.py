"""Generate ONE independently specified synthetic Nu-kote dataset, seed 42.
Run: python generate_nukote_data_v2.py
No optimizer is invoked. Rates, capacity slack and expansion costs are drawn
without observing solutions. Network counts, named plant/warehouse cities and
the 1.14 circuit factor follow the paper; coordinates are geographic proxies.
Vendor/customer locations, rates, costs, capacities and demand are synthetic.
A 15-day replenishment cycle is an explicit synthetic assumption.
Annual throughput is converted to average cycle inventory with 15/(2*365),
for BOTH holding cost and occupied pallet positions; no safety stock is modeled.
"""
from pathlib import Path
import math
import numpy as np
import pandas as pd
from openpyxl import load_workbook

SEED = 42
CYCLE_DAYS = 15      # replenishment interval used for average cycle stock
WW_DISCOUNT = 1.0    # inter-warehouse rate as a fraction of the TL base rate
OUTPUT = Path(__file__).resolve().with_name('nukote_synthetic_data_v2.xlsx')


def main(output=None):
    output = Path(output) if output is not None else OUTPUT
    rng = np.random.default_rng(SEED)
    pids = [str(i) for i in range(1, 7)]
    names = ['laser cartridges', 'ink-jet cartridges', 'ribbons',
             'thermal fax supplies', 'toner cartridges', 'other']
    weights = np.round(rng.uniform([2, .3, .2, .5, 3, .5],
                                   [3.5, .8, .6, 1.5, 6, 2]), 4)
    pallets = np.clip(np.rint(2200 / weights * rng.uniform(.85, 1.15, 6)),
                      400, 4000).astype(int)
    capital = round(float(rng.uniform(.10, .14)), 6)
    standard = np.round(rng.uniform(8, 120, 6), 4)
    annual_holding = capital * standard
    holding = annual_holding * CYCLE_DAYS / (2 * 365)
    products = pd.DataFrame(dict(product_id=pids, name=names, weight_lb=weights,
        units_per_pallet=pallets, std_cost_usd=standard,
        holding_cost_per_unit=holding, cost_of_capital=capital,
        annual_holding_cost_per_unit=annual_holding, replenishment_cycle_days=CYCLE_DAYS,
        pallets_per_annual_unit=CYCLE_DAYS/(2*365)/pallets))

    grand_total = int(rng.integers(4_000_000, 8_000_001))
    shares = rng.dirichlet([6, 5, 3, 2, 3, 2]) * rng.lognormal(0, .12, 6)
    shares /= shares.sum()
    totals = np.rint(grand_total * shares).astype(int)
    totals[np.argmax(totals)] += grand_total - totals.sum()
    total_pallets = float(np.sum(totals / pallets))
    # Set capacities immediately from demand totals, before locations or costs.
    capacity_factors = np.r_[rng.uniform(1.10, 1.30), rng.uniform(.30, .70, 3)]
    storage_pallets = total_pallets * CYCLE_DAYS/(2*365)
    warehouse_caps = np.ceil(storage_pallets * capacity_factors).astype(int)
    handling = np.round(rng.uniform(.03, .15, 4), 3)
    wids = ['W1', 'W2', 'W3', 'W4']
    warehouse = pd.DataFrame(dict(warehouse_id=wids,
        capacity_pallets=warehouse_caps, handling_cost_per_unit=handling))

    # 50 inland continental-US metro centers; region counts total 394.
    metros = {
      'Northeast': [('Albany NY',42.6526,-73.7562),('Buffalo NY',42.8864,-78.8784),
        ('Rochester NY',43.1566,-77.6088),('Syracuse NY',43.0481,-76.1474),
        ('Scranton PA',41.4090,-75.6624),('Harrisburg PA',40.2732,-76.8867),
        ('Allentown PA',40.6084,-75.4902),('Reading PA',40.3356,-75.9269),
        ('Worcester MA',42.2626,-71.8023),('Hartford CT',41.7658,-72.6734)],
      'Southeast': [('Atlanta GA',33.7490,-84.3880),('Charlotte NC',35.2271,-80.8431),
        ('Raleigh NC',35.7796,-78.6382),('Greensboro NC',36.0726,-79.7920),
        ('Columbia SC',34.0007,-81.0348),('Birmingham AL',33.5186,-86.8104),
        ('Nashville TN',36.1627,-86.7816),('Knoxville TN',35.9606,-83.9207),
        ('Richmond VA',37.5407,-77.4360),('Orlando FL',28.5383,-81.3792)],
      'Great Lakes/Ohio Valley/east Midwest': [('Columbus OH',39.9612,-82.9988),
        ('Cincinnati OH',39.1031,-84.5120),('Dayton OH',39.7589,-84.1916),
        ('Akron OH',41.0814,-81.5190),('Indianapolis IN',39.7684,-86.1581),
        ('Fort Wayne IN',41.0793,-85.1394),('Lansing MI',42.7325,-84.5555),
        ('Grand Rapids MI',42.9634,-85.6681),('Louisville KY',38.2527,-85.7585),
        ('Lexington KY',38.0406,-84.5037),('Pittsburgh PA',40.4406,-79.9959),
        ('South Bend IN',41.6764,-86.2520)],
      'Texas/South-central': [('Dallas TX',32.7767,-96.7970),
        ('Austin TX',30.2672,-97.7431),('San Antonio TX',29.4241,-98.4936),
        ('Oklahoma City OK',35.4676,-97.5164),('Tulsa OK',36.1540,-95.9928),
        ('Little Rock AR',34.7465,-92.2896)],
      'Mountain/Plains': [('Denver CO',39.7392,-104.9903),
        ('Colorado Springs CO',38.8339,-104.8214),('Boise ID',43.6150,-116.2023),
        ('Albuquerque NM',35.0844,-106.6504),('Wichita KS',37.6872,-97.3301),
        ('Omaha NE',41.2565,-95.9345)],
      'Pacific': [('Sacramento CA',38.5816,-121.4944),
        ('Fresno CA',36.7378,-119.7871),('Bakersfield CA',35.3733,-119.0187),
        ('Riverside CA',33.9806,-117.3755),('Salem OR',44.9429,-123.0351),
        ('Spokane WA',47.6588,-117.4260)]}
    region_counts = [98, 95, 114, 35, 16, 36]
    customers = []
    for (region, centers), count in zip(metros.items(), region_counts):
        for _ in range(count):
            name, lat, lon = centers[int(rng.integers(len(centers)))]
            jitter = np.clip(rng.normal(0, .035, 2), -.10, .10)
            customers.append((name, round(lat+jitter[0], 6),
                              round(lon+jitter[1], 6), region))
    rng.shuffle(customers)
    cids = [f'C{i:03d}' for i in range(1, 395)]
    fixed = [
      ('V1','vendor','Dallas TX',32.7767,-96.7970,'Texas/South-central',0),
      ('V2','vendor','Chicago IL',41.8781,-87.6298,'Great Lakes/Ohio Valley/east Midwest',0),
      ('V3','vendor','Atlanta GA',33.7490,-84.3880,'Southeast',0),
      ('V4','vendor','Long Beach CA',33.7701,-118.1937,'Pacific',0),
      ('V5','vendor','Overseas vendor (Long Beach port proxy)',33.7701,-118.1937,'Overseas',1),
      ('P1','plant','Chatsworth CA',34.2572,-118.6001,'Pacific',0),
      ('P2','plant','Franklin TN',35.9251,-86.8689,'Southeast',0),
      ('P3','plant','Rochester NY',43.1566,-77.6088,'Northeast',0),
      ('P4','plant','Connellsville PA',40.0173,-79.5895,'Northeast',0),
      ('P5','plant','Zhu Hai, China (Long Beach port proxy)',33.7701,-118.1937,'Overseas',1),
      ('W1','warehouse','Franklin TN',35.9251,-86.8689,'Southeast',0),
      ('W2','warehouse','Chatsworth CA',34.2572,-118.6001,'Pacific',0),
      ('W3','warehouse','Rochester NY',43.1566,-77.6088,'Northeast',0),
      ('W4','warehouse','Connellsville PA',40.0173,-79.5895,'Northeast',0)]
    locations = pd.DataFrame(fixed + [(cid,'customer',n,la,lo,r,0)
        for cid,(n,la,lo,r) in zip(cids,customers)],
        columns=['id','type','name','lat','lon','region','is_overseas'])

    q = rng.uniform(.30, .60, 6)
    largest = np.argsort(totals)[-2:]
    q[largest] = rng.uniform(.55, .90, 2)
    purchased = rng.random((394,6)) < q
    for i in np.flatnonzero(~purchased.any(axis=1)):
        purchased[i, rng.choice(6, p=shares)] = True
    raw = rng.lognormal(0,1,(394,1)) * rng.lognormal(0,.5,(394,6)) * purchased
    demand = np.rint(raw / raw.sum(axis=0) * totals).astype(int)
    for j in range(6):
        demand[np.argmax(raw[:,j]),j] += totals[j] - demand[:,j].sum()
    demand_df = pd.DataFrame([(c,p,int(demand[i,j])) for i,c in enumerate(cids)
        for j,p in enumerate(pids)], columns=['customer_id','product_id','annual_demand'])

    # Product eligibility is sampled without a regional sourcing quota.
    while True:
        eligible = np.zeros((10,6), dtype=bool)
        for i in range(10):
            k = int(rng.integers(1,5)) if i < 5 else int(rng.choice([1,2,3,4],p=[.4,.45,.1,.05]))
            eligible[i,rng.choice(6,k,replace=False)] = True
        if (eligible.sum(axis=0) >= 2).all():
            break
    supply = np.zeros((10,6), dtype=int)
    supply_factors = rng.uniform(1.15,1.35,6)
    for j in range(6):
        idx = np.flatnonzero(eligible[:,j])
        minimum = math.ceil(.05 * int(totals[j]))
        cap_total = math.ceil(totals[j]*supply_factors[j])
        remainder = cap_total-len(idx)*minimum
        allocation = np.rint(rng.dirichlet(np.ones(len(idx)))*remainder).astype(int)
        allocation[np.argmax(allocation)] += remainder-allocation.sum()
        supply[idx,j] = minimum+allocation
    vids = [f'V{i}' for i in range(1,6)]
    plantids = [f'P{i}' for i in range(1,6)]
    def supply_frame(ids, offset, column):
        return pd.DataFrame([(n,p,int(supply[i+offset,j])) for i,n in enumerate(ids)
            for j,p in enumerate(pids)],columns=[column,'product_id','capacity'])

    loc = locations.set_index('id')
    def miles(a,b):
        la1,lo1 = np.radians(loc.loc[a,['lat','lon']].to_numpy(dtype=float))
        la2,lo2 = np.radians(loc.loc[b,['lat','lon']].to_numpy(dtype=float))
        h = np.sin((la2-la1)/2)**2 + np.cos(la1)*np.cos(la2)*np.sin((lo2-lo1)/2)**2
        return round(float(3958.7613 * 2*np.arcsin(np.sqrt(np.clip(h,0,1))) * 1.14),1)
    lanes = [(v,w,'V-W') for v in vids for w in wids]
    lanes += [(p,w,'P-W') for p in plantids for w in wids]
    lanes += [(a,b,'W-W') for a in wids for b in wids if a != b]
    lanes += [(w,c,'W-C') for w in wids for c in cids]
    distances = pd.DataFrame([(a,b,t,miles(a,b)) for a,b,t in lanes],
                            columns=['from_id','to_id','lane_type','miles'])
    tl = float(rng.uniform(.000045,.000080))
    ltl = float(rng.uniform(.000110,.000160))
    ocean = float(rng.uniform(.02,.05))
    routes = []
    effective_rates = {'TL': [], 'LTL': []}
    for a,b,t,d in distances.itertuples(index=False,name=None):
        outbound = t == 'W-C'
        for j,p in enumerate(pids):
            multiplier = float(rng.lognormal(-.07**2/2,.07))
            rate = (ltl if outbound else tl)*multiplier*(WW_DISCOUNT if t == 'W-W' else 1.0)
            effective_rates['LTL' if outbound else 'TL'].append(rate)
            transport = round(rate*d*weights[j] + ocean*weights[j]*int(loc.at[a,'is_overseas']),4)
            hold = 0.0 if outbound else round(float(holding[j]),4)
            handle = 0.0 if outbound else float(handling[wids.index(b)])
            routes.append((a,b,t,p,transport,hold,handle,round(transport+hold+handle,4)))
    costs = pd.DataFrame(routes,columns=['from_id','to_id','lane_type','product_id',
        'transport_cost','holding_cost','handling_cost','unit_cost'])
    expansion = pd.DataFrame(dict(warehouse_id=wids, annual_fixed_cost_usd=
        [0]+[int(round(x/1000)*1000) for x in rng.uniform(150000,450000,3)]))
    sheets = dict(locations=locations, products=products, customer_demand=demand_df,
        vendor_capacity=supply_frame(vids,0,'vendor_id'),
        plant_capacity=supply_frame(plantids,5,'plant_id'), warehouse_capacity=warehouse,
        distances=distances,route_costs=costs,warehouse_expansion_costs=expansion)
    checks = []
    def check(name, condition, numbers):
        checks.append(dict(name=name,result='PASS' if bool(condition) else 'FAIL',
                           numbers=str(numbers),seed=SEED))
    for sheet,n in dict(locations=408,products=6,customer_demand=2364,
        vendor_capacity=30,plant_capacity=30,warehouse_capacity=4,distances=1628,
        route_costs=9768,warehouse_expansion_costs=4).items():
        check('row_count_'+sheet,len(sheets[sheet])==n,f'actual={len(sheets[sheet])}; expected={n}')
    for name,df in sheets.items():
        check('no_nan_'+name,not df.isna().any().any(),f'missing={df.isna().sum().sum()}')
        numeric = df.select_dtypes(include='number').drop(columns=['lat','lon'],errors='ignore')
        check('nonnegative_'+name,(numeric>=0).all().all(),'negative_count='+str((numeric<0).sum().sum()))
        for col in df.columns:
            if col == 'product_id':
                check(name+'_product_ids',set(df[col])==set(pids) and df[col].map(lambda x:isinstance(x,str)).all(),len(set(df[col])))
            elif col in ['from_id','to_id','customer_id','vendor_id','plant_id','warehouse_id','id']:
                allowed = set(locations.id) if col in ['id','from_id','to_id'] else set(locations.loc[locations.type==col.removesuffix('_id'),'id'])
                check(name+'_'+col,set(df[col])<=allowed and df[col].map(lambda x:isinstance(x,str)).all(),f'unique={df[col].nunique()}')
    for j,p in enumerate(pids):
        check('product_balance_'+p,demand[:,j].sum()==totals[j] and 1.15<=supply[:,j].sum()/totals[j]<=1.351,
              f'supply={supply[:,j].sum()}; demand={demand[:,j].sum()}; ratio={supply[:,j].sum()/totals[j]:.4f}')
        check('supply_coverage_'+p,eligible[:,j].sum()>=2 and (supply[eligible[:,j],j]>=.05*totals[j]).all(),
              f'positive_nodes={eligible[:,j].sum()}; smallest_capacity={supply[eligible[:,j],j].min()}; minimum={.05*totals[j]}')
    check('node_product_counts',((eligible.sum(axis=1)>=1)&(eligible.sum(axis=1)<=4)).all(),eligible.sum(axis=1).tolist())
    check('every_customer_positive',(demand.sum(axis=1)>0).all(),f'positive_customers={(demand.sum(axis=1)>0).sum()}')
    east = float((locations.loc[locations.type=='customer','lon']>-90).mean())
    check('east_customer_share',.74<=east<=.82,f'east_share={east}; east_count={round(east*394)}; total=394')
    check('warehouse_total_capacity',warehouse_caps.sum()>=storage_pallets,f'capacity={warehouse_caps.sum()}; average_inventory_pallets={storage_pallets}')
    check('franklin_capacity',warehouse_caps[0]>=storage_pallets,f'W1={warehouse_caps[0]}; average_inventory_pallets={storage_pallets}')
    joined = costs.merge(distances,on=['from_id','to_id','lane_type'],validate='many_to_one')
    for (p,t),group in joined.groupby(['product_id','lane_type']):
        corr = group.unit_cost.corr(group.miles)
        check(f'cost_distance_correlation_{p}_{t}',np.isfinite(corr) and corr>0,f'correlation={corr:.8f}; n={len(group)}')
    mean_tl = float(np.mean(effective_rates['TL']))
    mean_ltl = float(np.mean(effective_rates['LTL']))
    check('LTL_rate_exceeds_TL',mean_ltl>mean_tl,
        f'mean_LTL={mean_ltl}; mean_TL={mean_tl}; base_LTL={ltl}; base_TL={tl}; ocean_per_lb={ocean}; noise_sigma=0.07')
    check('holding_formula',np.allclose(holding,capital*standard*CYCLE_DAYS/(2*365),rtol=0,atol=1e-12),f'cost_of_capital={capital}; cycle_days={CYCLE_DAYS}')
    check('storage_holding_period_consistency',np.allclose(products.pallets_per_annual_unit*products.units_per_pallet,CYCLE_DAYS/730,rtol=0,atol=1e-14),f'average_stock_fraction={CYCLE_DAYS/730}')
    check('pallet_load_plausibility',bool(((weights*pallets>=400)&(weights*pallets<=3000)).all()),f'lb_per_pallet={(weights*pallets).tolist()}; cube not modeled')
    check('unit_cost_sum',np.allclose(costs.unit_cost,costs[['transport_cost','holding_cost','handling_cost']].sum(axis=1),atol=.0000001),f'rows={len(costs)}')
    check('zero_distance_transport',(joined.loc[joined.miles==0,'transport_cost']==0).all(),f'zero_mile_rows={(joined.miles==0).sum()}')
    sheets['assumptions'] = pd.DataFrame([
        ('seed',SEED,'fixed seed'),
        ('TL_rate',tl,'synthetic USD/lb-mile, all inbound and interwarehouse lanes'),
        ('LTL_rate',ltl,'synthetic USD/lb-mile, outbound lanes'),
        ('ocean_surcharge',ocean,'synthetic USD/lb'),
        ('noise_sigma',.07,'mean-one lognormal multiplier'),
        ('cycle_days',CYCLE_DAYS,'synthetic replenishment interval; average stock = annual flow * cycle_days/730'),
        ('WW_discount',WW_DISCOUNT,'no interwarehouse discount'),
        ('annual_throughput_pallets',total_pallets,'annual pallet-equivalent receipts, not physical storage'),
        ('average_inventory_pallets',storage_pallets,'physical cycle-stock pallets for single-pass flow'),
    ],columns=['parameter','value','rationale'])
    sheets['checks'] = pd.DataFrame(checks)
    failures = sheets['checks'].query("result == 'FAIL'")
    if not failures.empty:
        raise AssertionError('Validation failed:\n'+failures.to_string(index=False))
    with pd.ExcelWriter(output,engine='openpyxl') as writer:
        for name,df in sheets.items():
            df.to_excel(writer,sheet_name=name,index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                header = col[0].value
                text_width = max((len(str(cell.value)) for cell in col[:400] if isinstance(cell.value,str)),default=len(str(header)))
                ws.column_dimensions[col[0].column_letter].width = min(65,max(14,text_width+2))
                if name=='route_costs' and header.endswith('cost'):
                    for cell in col[1:]: cell.number_format='0.0000'
                elif name=='distances' and header=='miles':
                    for cell in col[1:]: cell.number_format='0.0'
                elif header=='handling_cost_per_unit':
                    for cell in col[1:]: cell.number_format='0.000'
    # Check serialized Excel IDs remain strings and all sheets survived export.
    wb = load_workbook(output,read_only=True,data_only=True)
    assert wb.sheetnames == list(sheets), 'Excel sheet mismatch'
    for name,df in sheets.items():
        ws = wb[name]
        assert ws.max_row == len(df)+1, f'Excel row count mismatch: {name}'
        id_indices = [i for i,c in enumerate(df.columns) if c=='id' or c.endswith('_id')]
        for row in ws.iter_rows(min_row=2,values_only=True):
            assert all(isinstance(row[i],str) for i in id_indices), f'Non-string Excel ID in {name}'
    wb.close()
    print(f'Seed: {SEED}; {len(checks)} checks; FAIL=0; WARN='+str(int((sheets['checks'].result=='WARN').sum())))
    print(sheets['checks'].query("result == 'WARN'").to_string(index=False))
    for j,p in enumerate(pids):
        print(f'Product {p} ({names[j]}): {totals[j]:,} units; eligible suppliers: {eligible[:,j].sum()}')
    print(f'Total units: {grand_total:,}; annual throughput pallets: {total_pallets:,.4f}; average storage pallets: {storage_pallets:,.4f}')
    print('Warehouse capacities: '+', '.join(f'{w}={c:,}' for w,c in zip(wids,warehouse_caps)))
    print(f'Saved: {output}')


if __name__ == '__main__':
    main()
