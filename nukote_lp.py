"""Nu-kote-inspired annual continuous-flow LP on synthetic v2 inputs.
Run: python nukote_lp.py [nukote_synthetic_data_v2.xlsx] [output_dir]
Tests: python nukote_lp.py --self-test

Sources have product-specific upper bounds. Warehouse/product inflow equals
outflow; customer/product demand is exact. Receipts consume physical average
cycle-stock pallet positions using the explicit flow-to-stock product parameter.
All warehouse receipts incur holding and destination handling. Outbound selection
controls only W-C arcs; factory warehouses remain usable. If no selected outbound
warehouse is within the radius, the nearest is permitted as a disclosed synthetic
exception policy (not a rule specified by the paper). Fixed fees are exogenous
and charged once per outbound warehouse. All eight Franklin-retaining subsets
are enumerated. SciPy/HiGHS is preferred; a NumPy primal-dual solver is available
when SciPy is missing. Every solution must pass primal and dual validation.
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import openpyxl

WIDS = ['W1', 'W2', 'W3', 'W4']
TAG = {'W1': 'T', 'W2': 'C', 'W3': 'N', 'W4': 'P'}   # Tennessee, California, New York, Pennsylvania
ID_COLS = ['id', 'from_id', 'to_id', 'customer_id', 'product_id', 'vendor_id', 'plant_id', 'warehouse_id']


class CoordinateMatrix:
    """Minimal coordinate storage for the NumPy fallback; no SciPy emulation."""
    def __init__(self, argument, shape):
        values, (rows, columns) = argument
        self.data = np.asarray(values,dtype=float)
        self.row = np.asarray(rows,dtype=int)
        self.col = np.asarray(columns,dtype=int)
        self.shape = shape

    def tocsr(self):
        return self


def numpy_linprog(c, A_ub, b_ub, A_eq, b_eq, **unused):
    """Mehrotra predictor-corrector LP, specialized block normal equations.

    Standard form Ax=b, x>=0 is solved after adding inequality slacks.
    Demand rows have disjoint column support. Their diagonal normal-equation
    block is eliminated, leaving at most 88 core rows for this network.
    No approximate optimization result is accepted: primal/dual residuals and
    duality gap must converge. A returned infeasibility requires a certificate.
    This fallback is independently implemented, not HiGHS or Excel Solver.
    """
    from types import SimpleNamespace
    n = len(c)
    # Positive-only zero RHS rows force their variables to zero exactly.
    zero = np.zeros(n,dtype=bool)
    for A,b in [(A_eq,b_eq),(A_ub,b_ub)]:
        negatives = np.bincount(A.row,weights=(A.data<0),minlength=len(b))
        forced = (np.asarray(b)==0)&(negatives==0)
        zero[A.col[forced[A.row] & (A.data>0)]] = True
    active = np.flatnonzero(~zero)
    reverse = np.full(n,-1,dtype=int); reverse[active] = np.arange(len(active))
    def compressed(A,b):
        keep = ~zero[A.col]
        r,cl,v = A.row[keep],reverse[A.col[keep]],A.data[keep]
        counts = np.bincount(r,minlength=len(b))
        empty_violation = (counts==0)&(np.abs(b)>1e-12)
        ids = np.flatnonzero(counts>0)
        remap = np.full(len(b),-1,dtype=int); remap[ids] = np.arange(len(ids))
        return remap[r],cl,v,np.asarray(b)[ids],ids,bool(empty_violation.any())
    er,ec,ev,eb,eids,empty = compressed(A_eq,b_eq)
    ur,uc,uv,ub,uids,_ = compressed(A_ub,b_ub)
    if empty:
        return SimpleNamespace(status=2,message='Certified infeasible: nonzero equality RHS with no available variables.')
    nv = len(active); nu = len(ub)
    # Demand equality rows have nonnegative coefficients and positive RHS.
    negative = np.bincount(er,weights=(ev<0),minlength=len(eb))
    demand_rows = np.flatnonzero((negative==0)&(eb>0))
    core_rows = np.flatnonzero(~np.isin(np.arange(len(eb)),demand_rows))
    nd,ncore = len(demand_rows),len(core_rows)+nu
    dm = np.full(len(eb),-1,dtype=int); dm[demand_rows] = np.arange(nd)
    cm = np.full(len(eb),-1,dtype=int); cm[core_rows] = np.arange(len(core_rows))
    flow_scale = max(1.,float(np.max(np.abs(np.r_[eb,ub]))))
    escale = np.where(eb==0,flow_scale,np.maximum(np.abs(eb),flow_scale*1e-4))
    uscale = np.maximum(np.abs(ub),1.)
    ev = ev*flow_scale/escale[er]
    uv = uv*flow_scale/uscale[ur]
    Ac = np.zeros((ncore,nv+nu))
    core_mask = cm[er]>=0
    np.add.at(Ac,(cm[er[core_mask]],ec[core_mask]),ev[core_mask])
    np.add.at(Ac,(len(core_rows)+ur,uc),uv)
    Ac[len(core_rows)+np.arange(nu),nv+np.arange(nu)] = 1
    dc = np.full(nv+nu,-1,dtype=int)
    ad = np.zeros(nv+nu)
    mask = dm[er]>=0
    for r,cl,val in zip(dm[er[mask]],ec[mask],ev[mask]):
        if dc[cl]>=0:
            raise ValueError('Fallback requires disjoint demand rows')
        dc[cl],ad[cl] = r,val
    wi = np.flatnonzero(dc>=0)
    wd = dc[wi]
    b = np.r_[eb[demand_rows]/escale[demand_rows],eb[core_rows]/escale[core_rows],ub/uscale]
    cost_scale = max(1.,float(np.max(np.abs(c))))
    objective_scale = cost_scale*flow_scale
    cs = np.r_[np.asarray(c)[active]/cost_scale,np.zeros(nu)]
    def A(v):
        return np.r_[np.bincount(wd,weights=ad[wi]*v[wi],minlength=nd),Ac@v]
    def AT(v):
        out = Ac.T@v[nd:]
        out[wi] += ad[wi]*v[wd]
        return out
    x = np.ones(nv+nu); slack = np.ones(nv+nu); y = np.zeros(len(b))
    primal_target = min(1e-8,1e-6/max(float(np.max(escale)),float(np.max(uscale))))
    gap_target = min(.0005,max(1e-7,objective_scale*1e-11))
    def step_length(v,d):
        neg = d<0
        return min(1.,float(np.min(-v[neg]/d[neg]))) if neg.any() else 1.
    iterations = 0
    for iterations in range(180):
        rb = b-A(x); rc = cs-AT(y)-slack
        mu = float(x@slack/len(x))
        primal_error = float(np.max(np.abs(rb)))
        dual_error = float(np.max(np.abs(rc)))
        gap = abs(float(cs@x-b@y))
        if unused.get('debug',False):
            print(iterations,primal_error,dual_error,gap*objective_scale,mu,flush=True)
        if primal_error<primal_target and dual_error<2e-12 and gap*objective_scale<gap_target:
            break
        D = x/slack
        # Weighted covariance form avoids subtracting nearly equal normal blocks.
        dsums = np.bincount(wd,weights=D[wi],minlength=nd)
        mean = np.zeros((nd,ncore))
        np.add.at(mean,wd,(Ac[:,wi]*D[wi]).T)
        mean /= dsums[:,None]
        centered = Ac.copy()
        centered[:,wi] -= mean[wd].T
        schur = (centered*D)@centered.T
        adi = np.zeros(nd)
        adi[wd] = ad[wi]
        diagonal = adi**2*dsums
        def direction(center):
            v = (center-x*rc)/slack
            rhs = rb-A(v)
            rhs_core = rhs[nd:]-mean.T@(rhs[:nd]/adi)
            try:
                core_y = np.linalg.solve(schur,rhs_core)
            except np.linalg.LinAlgError:
                core_y = np.linalg.lstsq(schur,rhs_core,rcond=1e-14)[0]
            # One refinement improves core residuals near degeneracy.
            residual = rhs_core-schur@core_y
            if np.max(np.abs(residual))>1e-11:
                core_y += np.linalg.lstsq(schur,residual,rcond=1e-14)[0]
            demand_y = rhs[:nd]/diagonal-(mean@core_y)/adi
            dy = np.r_[demand_y,core_y]
            ds = rc-AT(dy)
            dx = (center-x*ds)/slack
            return dx,dy,ds
        dx,dy,ds = direction(-x*slack)
        ap,adual = step_length(x,dx),step_length(slack,ds)
        mu_affine = max(0.,float((x+ap*dx)@(slack+adual*ds)/len(x)))
        sigma = min(1.,(mu_affine/mu)**3)
        dx,dy,ds = direction(-x*slack+sigma*mu-dx*ds)
        ap = min(1.,.995*step_length(x,dx)); adual = min(1.,.995*step_length(slack,ds))
        x += ap*dx; y += adual*dy; slack += adual*ds
        if not (np.isfinite(x).all() and np.isfinite(y).all() and np.isfinite(slack).all()):
            raise RuntimeError('NumPy LP numerical failure; no solution accepted')
        # Farkas: A.T y <= 0 and b.T y > 0 certifies Ax=b,x>=0 infeasible.
        ynorm = np.max(np.abs(y))
        if ynorm>1e8:
            cert = y/ynorm
            if np.max(AT(cert))<=1e-10 and b@cert>1e-6:
                return SimpleNamespace(status=2,message=f'Farkas infeasibility certificate: max(A.T y)={np.max(AT(cert))}; b.T y={b@cert}')
    else:
        raise RuntimeError(f'NumPy LP did not converge: primal={primal_error}; dual={dual_error}; gap_USD={gap*objective_scale}')
    full_x = np.zeros(n); full_x[active] = x[:nv]*flow_scale
    emarginal = np.zeros(len(b_eq)); umarginal = np.zeros(len(b_ub))
    emarginal[eids[demand_rows]] = y[:nd]*objective_scale/escale[demand_rows]
    emarginal[eids[core_rows]] = y[nd:nd+len(core_rows)]*objective_scale/escale[core_rows]
    umarginal[uids] = y[nd+len(core_rows):]*objective_scale/uscale
    # Recover dual multipliers for eliminated zero-demand/zero-capacity rows.
    # Their zero RHS means these adjustments do not change the dual objective.
    reduced = np.asarray(c).copy()
    np.add.at(reduced,A_eq.col,-A_eq.data*emarginal[A_eq.row])
    np.add.at(reduced,A_ub.col,-A_ub.data*umarginal[A_ub.row])
    for original,b_original,marginal in [(A_eq,b_eq,emarginal),(A_ub,b_ub,umarginal)]:
        negative_rows = np.bincount(original.row,weights=(original.data<0),minlength=len(b_original))
        for row in np.flatnonzero((np.asarray(b_original)==0)&(negative_rows==0)):
            mask = (original.row==row)&(original.data>0)
            cols,values = original.col[mask],original.data[mask]
            if len(cols) and np.min(reduced[cols])<0:
                shift = max(0.,float(np.max(-reduced[cols]/values)))+1e-12
                marginal[row] -= shift
                np.add.at(reduced,cols,shift*values)
    return SimpleNamespace(status=0,message=f'NumPy Mehrotra primal-dual LP converged in {iterations} iterations',
        x=full_x,fun=float(np.asarray(c)@full_x),eqlin=SimpleNamespace(marginals=emarginal),
        ineqlin=SimpleNamespace(marginals=umarginal),certificate_gap_usd=gap*objective_scale,
        primal_residual_scaled=primal_error,dual_residual_scaled=dual_error)


def load(path):
    return pd.read_excel(path, sheet_name=None, dtype={c: str for c in ID_COLS})


def solve(S, outbound=('W1',), radius=1000.0, validate=True, solver='auto'):
    """Solve one LP. S = dict of DataFrames (workbook sheets). Returns a dict of results."""
    if solver not in ['auto','highs','numpy']:
        raise ValueError('solver must be auto, highs or numpy')
    if solver == 'numpy':
        linprog,coo_matrix = numpy_linprog,CoordinateMatrix
        backend = 'NumPy Mehrotra primal-dual LP'
    else:
        try:
            from scipy.optimize import linprog
            from scipy.sparse import coo_matrix
            backend = 'SciPy/HiGHS'
        except ImportError:
            if solver == 'highs':
                raise ImportError('SciPy/HiGHS requested but SciPy is not installed')
            linprog,coo_matrix = numpy_linprog,CoordinateMatrix
            backend = 'NumPy Mehrotra primal-dual LP'
    outbound = list(outbound)
    if not outbound or len(set(outbound)) != len(outbound) or not set(outbound) <= set(WIDS):
        raise ValueError('Outbound warehouses must be unique valid warehouse IDs')
    if radius < 0:
        raise ValueError('Radius must be nonnegative')
    loc, dist, rc = S['locations'], S['distances'], S['route_costs']
    ppal = S['products'].set_index('product_id').units_per_pallet.to_dict()
    pids = list(S['products'].product_id)
    cap_w = S['warehouse_capacity'].set_index('warehouse_id').capacity_pallets.to_dict()

    # Arc set: all inbound + warehouse-warehouse lanes; warehouse->customer only within radius
    d = dist.set_index(['from_id', 'to_id']).miles
    customers = loc.loc[loc.type == 'customer', 'id'].tolist()
    keep = ~((dist.lane_type == 'W-C') & ~dist.from_id.isin(outbound))
    arcs = dist[keep][['from_id', 'to_id', 'lane_type', 'miles']].copy()
    wc = arcs.lane_type == 'W-C'
    arcs = arcs[~wc | (arcs.miles <= radius)]
    have = set(arcs.loc[arcs.lane_type == 'W-C', 'to_id'])
    exceptions = []
    extra = []
    for c in customers:
        if c not in have:
            w = min(outbound, key=lambda k: d[(k, c)])
            extra.append((w, c, 'W-C', d[(w, c)]))
            exceptions.append(c)
    if extra:
        arcs = pd.concat([arcs, pd.DataFrame(extra, columns=arcs.columns)], ignore_index=True)

    V = arcs.merge(rc[['from_id', 'to_id', 'product_id', 'unit_cost', 'transport_cost', 'holding_cost',
                       'handling_cost']], on=['from_id', 'to_id'], how='left').reset_index(drop=True)
    n = len(V)
    c_vec = V.unit_cost.to_numpy()

    def rows_for(key_series, mask, keys):
        """Row index per variable for constraint keys (variables outside mask get -1)."""
        lookup = {k: i for i, k in enumerate(keys)}
        return np.array([lookup.get(k, -1) if m else -1 for k, m in zip(key_series, mask)])

    # ---- equality constraints -------------------------------------------------------------
    dem = S['customer_demand'].set_index(['customer_id', 'product_id']).annual_demand
    dkeys = list(dem.index)
    is_wc = (V.lane_type == 'W-C').to_numpy()
    inflow = (V.to_id.isin(WIDS) & (V.from_id != V.to_id)).to_numpy()
    outflow = V.from_id.isin(WIDS).to_numpy()
    bkeys = [(w, p) for w in WIDS for p in pids]
    off = len(dkeys)
    rows, cols, vals = [], [], []

    def add(r, mask, sign, offset=0):
        idx = np.flatnonzero(mask & (r >= 0))
        rows.extend((r[idx] + offset).tolist()); cols.extend(idx.tolist()); vals.extend([sign] * len(idx))

    add(rows_for(zip(V.to_id, V.product_id), is_wc, dkeys), is_wc, 1.0)                    # customer demand
    add(rows_for(zip(V.to_id, V.product_id), inflow, bkeys), inflow, 1.0, off)             # warehouse inflow
    add(rows_for(zip(V.from_id, V.product_id), outflow, bkeys), outflow, -1.0, off)        # warehouse outflow
    Aeq = coo_matrix((vals, (rows, cols)), shape=(len(dkeys) + len(bkeys), n)).tocsr()
    beq = np.r_[dem.to_numpy(float), np.zeros(len(bkeys))]

    # ---- inequality constraints -----------------------------------------------------------
    sup = pd.concat([S['vendor_capacity'].rename(columns={'vendor_id': 'src'}),
                     S['plant_capacity'].rename(columns={'plant_id': 'src'})], ignore_index=True)
    skeys = list(zip(sup.src, sup.product_id))
    is_src = V.lane_type.isin(['V-W', 'P-W']).to_numpy()
    r_sup = rows_for(zip(V.from_id, V.product_id), is_src, skeys)
    ok = r_sup >= 0
    rows, cols, vals = list(r_sup[ok]), list(np.flatnonzero(ok)), [1.0] * int(ok.sum())
    wk = {w: len(skeys) + i for i, w in enumerate(WIDS)}
    coefficients = S['products'].set_index('product_id')
    coefficients = (coefficients.pallets_per_annual_unit if 'pallets_per_annual_unit' in coefficients else 1/coefficients.units_per_pallet)
    pal = V.product_id.map(coefficients).to_numpy()
    for i in np.flatnonzero(inflow):
        rows.append(wk[V.to_id.iat[i]]); cols.append(i); vals.append(pal[i])
    Aub = coo_matrix((vals, (rows, cols)), shape=(len(skeys) + len(WIDS), n)).tocsr()
    bub = np.r_[sup.capacity.to_numpy(float), [cap_w[w] for w in WIDS]]

    res = linprog(c_vec, A_ub=Aub, b_ub=bub, A_eq=Aeq, b_eq=beq, bounds=(0, None), method='highs')
    out = dict(status=res.status, message=res.message, backend=backend, n_vars=n, n_cons=Aeq.shape[0] + Aub.shape[0],
               exceptions=exceptions, V=V, outbound=outbound, radius=radius)
    if res.status != 0:
        out['objective'] = np.nan
        if res.status != 2:
            raise RuntimeError(f'LP solver error {res.status}: {res.message}')
        return out
    x = res.x
    V = V.assign(flow=x)
    out.update(objective=float(res.fun), V=V)
    out['transport'] = float((x * V.transport_cost).sum())
    out['holding'] = float((x * V.holding_cost).sum())
    out['handling'] = float((x * V.handling_cost).sum())
    out['inbound_transport'] = float((x * V.transport_cost)[V.lane_type.isin(['V-W', 'P-W'])].sum())
    out['ww_units'] = float(V.loc[V.lane_type == 'W-W', 'flow'].sum())
    pallets = {w: float((x * pal)[inflow & (V.to_id == w).to_numpy()].sum()) for w in WIDS}
    out['pallets_used'] = pallets
    out['capacity'] = cap_w
    out['pallet_dual'] = {w: float(res.ineqlin.marginals[wk[w]]) for w in WIDS}
    out['supply_dual'] = pd.DataFrame({'src': sup.src, 'product_id': sup.product_id,
                                       'capacity': sup.capacity,
                                       'shadow_price': res.ineqlin.marginals[:len(skeys)]})
    src_flow = V[V.lane_type.isin(['V-W', 'P-W'])].groupby('from_id').flow.sum()
    ca = src_flow.reindex(['V4', 'V5', 'P1', 'P5']).fillna(0).sum()
    out['inbound_ca_overseas_share'] = float(ca / src_flow.sum()) if src_flow.sum() else 0.0
    fixed = S['warehouse_expansion_costs'].set_index('warehouse_id').annual_fixed_cost_usd
    out['fixed_cost'] = float(fixed.reindex(outbound).sum())
    out['total_cost'] = out['objective'] + out['fixed_cost']
    # Dual feasibility and objective gap certify optimality separately from primal checks.
    dual_eq,dual_ub = res.eqlin.marginals,res.ineqlin.marginals
    if backend == 'SciPy/HiGHS':
        reduced = c_vec-Aeq.T@dual_eq-Aub.T@dual_ub
    else:
        reduced = c_vec.copy()
        np.add.at(reduced,Aeq.col,-Aeq.data*dual_eq[Aeq.row])
        np.add.at(reduced,Aub.col,-Aub.data*dual_ub[Aub.row])
    out['dual_objective'] = float(beq@dual_eq+bub@dual_ub)
    out['duality_gap_usd'] = abs(out['objective']-out['dual_objective'])
    out['dual_max_inequality_marginal'] = float(max(dual_ub))
    out['min_reduced_cost'] = float(min(reduced))
    out['certificate_gap_usd'] = getattr(res,'certificate_gap_usd',out['duality_gap_usd'])
    out['certificate_dual_residual'] = getattr(res,'dual_residual_scaled',0.0)
    out['eligible_wc_arcs'] = int((arcs.lane_type == 'W-C').sum())
    out['validation'] = validate_solution(S, out) if validate else []
    return out


def validate_solution(S, result):
    """Recompute constraints from flow records, independently of LP matrices.

    Absolute tolerances: 1e-5 units/pallets, $0.01 + 1e-9 * objective.
    Validation does not use the assembled solver constraint matrices or slacks.
    """
    V = result['V']
    records = V.to_dict('records')
    checks = []
    def test(name, ok, detail):
        checks.append(dict(name=name, result='PASS' if ok else 'FAIL', detail=str(detail)))
    tolerance = 1e-5
    test('finite_nonnegative_flow', bool(np.isfinite(V.flow).all() and V.flow.min() >= -tolerance), V.flow.min())
    incoming, outgoing, delivered, sourced = {}, {}, {}, {}
    lane_records = S['distances'].set_index(['from_id','to_id']).to_dict('index')
    raw_costs = S['route_costs'].set_index(['from_id','to_id','product_id']).to_dict('index')
    product = S['products'].set_index('product_id').to_dict('index')
    wh = S['warehouse_capacity'].set_index('warehouse_id').to_dict('index')
    raw_wc = S['distances'].query("lane_type == 'W-C'")
    allowed = set()
    exception_ids = set()
    for customer, rows in raw_wc.groupby('to_id'):
        rows = rows[rows.from_id.isin(result['outbound'])]
        local = rows[rows.miles <= result['radius']]
        if local.empty:
            exception_ids.add(customer)
            # Deterministic tie rule follows outbound order, independent of row order.
            by_id = rows.set_index('from_id').miles
            nearest = min(result['outbound'], key=lambda w: by_id[w])
            allowed.add((nearest,customer))
        else:
            allowed.update(zip(local.from_id,local.to_id))
    illegal, reconstruction = [], 0.0
    stock = dict.fromkeys(WIDS, 0.0)
    for row in records:
        a,b,p,f = row['from_id'],row['to_id'],row['product_id'],row['flow']
        lane = lane_records.get((a,b))
        c = raw_costs.get((a,b,p))
        valid = lane is not None and c is not None and a != b and lane['lane_type'] == row['lane_type']
        if row['lane_type'] == 'W-C':
            valid = valid and (a,b) in allowed
            delivered[b,p] = delivered.get((b,p),0)+f
        if not valid:
            illegal.append((a,b,p,f))
        if c is not None:
            reconstruction += f*(c['transport_cost']+c['holding_cost']+c['handling_cost'])
        if b in WIDS:
            incoming[b,p] = incoming.get((b,p),0)+f
            factor = product[p].get('pallets_per_annual_unit',1/product[p]['units_per_pallet'])
            stock[b] += f*factor
        if a in WIDS:
            outgoing[a,p] = outgoing.get((a,p),0)+f
        if row['lane_type'] in ['V-W','P-W']:
            sourced[a,p] = sourced.get((a,p),0)+f
    demand_errors = [abs(delivered.get((r.customer_id,r.product_id),0)-r.annual_demand)
                     for r in S['customer_demand'].itertuples(index=False)]
    test('customer_demand',max(demand_errors,default=0)<=tolerance,max(demand_errors,default=0))
    excess = []
    for name,col in [('vendor_capacity','vendor_id'),('plant_capacity','plant_id')]:
        for r in S[name].to_dict('records'):
            excess.append(sourced.get((r[col],r['product_id']),0)-r['capacity'])
    test('source_capacities',max(excess,default=0)<=tolerance,max(excess,default=0))
    balance = [abs(incoming.get((w,p),0)-outgoing.get((w,p),0)) for w in WIDS for p in product]
    test('warehouse_product_balance',max(balance,default=0)<=tolerance,max(balance,default=0))
    stock_excess = max(stock[w]-wh[w]['capacity_pallets'] for w in WIDS)
    test('warehouse_storage_capacity',stock_excess<=tolerance,stock_excess)
    test('allowed_routes',not illegal,f'illegal_variable_records={illegal[:5]}')
    test('exception_policy',exception_ids==set(result['exceptions']),f'exception_customers={len(exception_ids)}')
    test('eligible_arc_count',len(allowed)==result['eligible_wc_arcs'],len(allowed))
    fixed = S['warehouse_expansion_costs'].set_index('warehouse_id').annual_fixed_cost_usd.to_dict()
    expected_fixed = sum(fixed[w] for w in set(result['outbound']))
    test('fixed_cost_once',abs(expected_fixed-result['fixed_cost'])<1e-8,f'expected={expected_fixed}; charged={result["fixed_cost"]}')
    cost_tolerance = .01 + 1e-9*abs(result['objective'])
    test('independent_variable_objective',abs(reconstruction-result['objective'])<=cost_tolerance,
         f'rebuilt={reconstruction}; solver={result["objective"]}; tolerance={cost_tolerance}')
    test('independent_total_objective',abs(reconstruction+expected_fixed-result['total_cost'])<=cost_tolerance,
         reconstruction+expected_fixed-result['total_cost'])
    test('primal_dual_objective_gap',result['duality_gap_usd']<=cost_tolerance,
         f'gap_USD={result["duality_gap_usd"]}; dual_objective={result["dual_objective"]}')
    test('dual_certificate',result['dual_max_inequality_marginal']<=1e-8 and result['certificate_dual_residual']<=2e-12 and
         result['min_reduced_cost']>=-1e-8,
         f'backend={result["backend"]}; scaled_dual_residual={result["certificate_dual_residual"]}; minimum_reduced_cost={result["min_reduced_cost"]}')
    if any(c['result']=='FAIL' for c in checks):
        raise AssertionError(pd.DataFrame(checks).query("result=='FAIL'").to_string(index=False))
    return checks


def input_checks(S):
    checks = []
    def test(name, condition, detail):
        checks.append(dict(name=name,result='PASS' if condition else 'FAIL',detail=str(detail)))
    expected = dict(locations=408,products=6,customer_demand=2364,vendor_capacity=30,
                    plant_capacity=30,warehouse_capacity=4,distances=1628,route_costs=9768,warehouse_expansion_costs=4)
    for name,n in expected.items():
        df = S[name]
        test('rows_'+name,len(df)==n,f'{len(df)} == {n}')
        test('complete_'+name,not df.isna().any().any(),f'NaN={df.isna().sum().sum()}')
        numeric = df.select_dtypes('number').drop(columns=['lat','lon'],errors='ignore')
        test('nonnegative_finite_'+name,bool(np.isfinite(numeric).all().all() and (numeric>=0).all().all()),len(numeric))
    locations = S['locations'].set_index('id')
    test('unique_locations',locations.index.is_unique,len(locations))
    test('unique_products',S['products'].product_id.is_unique and set(S['products'].product_id)==set('123456'),S['products'].product_id.tolist())
    test('positive_product_weights_and_pack_sizes',bool((S['products'][['weight_lb','units_per_pallet']]>0).all().all()),'lb/unit and units/pallet > 0')
    test('network_node_counts',locations.type.value_counts().to_dict()==dict(customer=394,vendor=5,plant=5,warehouse=4),locations.type.value_counts().to_dict())
    for name,keys in [('customer_demand',['customer_id','product_id']),('vendor_capacity',['vendor_id','product_id']),
                      ('plant_capacity',['plant_id','product_id']),('distances',['from_id','to_id']),
                      ('route_costs',['from_id','to_id','product_id'])]:
        test('unique_keys_'+name,not S[name].duplicated(keys).any(),keys)
    for name,df in S.items():
        for col in [c for c in ID_COLS if c in df]:
            allowed = set(S['products'].product_id) if col=='product_id' else set(locations.index)
            if col in ['customer_id','vendor_id','plant_id','warehouse_id']:
                allowed = set(locations[locations.type==col.removesuffix('_id')].index)
            test(name+'_'+col+'_references',set(df[col])<=allowed and df[col].map(lambda x:isinstance(x,str)).all(),df[col].nunique())
    counts = S['distances'].lane_type.value_counts().to_dict()
    test('lane_counts',counts=={'V-W':20,'P-W':20,'W-W':12,'W-C':1576},counts)
    valid_lanes = True
    types = {'V-W':('vendor','warehouse'),'P-W':('plant','warehouse'),
             'W-W':('warehouse','warehouse'),'W-C':('warehouse','customer')}
    for a,b,t,_ in S['distances'].itertuples(index=False,name=None):
        valid_lanes &= a!=b and (locations.at[a,'type'],locations.at[b,'type'])==types[t]
    test('lane_topology_no_self_loops',valid_lanes,'all four lane types checked')
    costs = S['route_costs'].merge(S['distances'],on=['from_id','to_id','lane_type'],validate='many_to_one')
    test('cost_decomposition',bool(np.allclose(costs.unit_cost,costs[['transport_cost','holding_cost','handling_cost']].sum(axis=1),rtol=0,atol=1e-8)),len(costs))
    test('cost_lane_product_coverage',len(costs)==len(S['distances'])*len(S['products']),len(costs))
    pp = S['products'].set_index('product_id')
    wh = S['warehouse_capacity'].set_index('warehouse_id')
    inbound = costs.lane_type!='W-C'
    test('holding_destination_lanes',bool(np.allclose(costs.holding_cost,np.where(inbound,costs.product_id.map(pp.holding_cost_per_unit).round(4),0),atol=1e-8,rtol=0)),'warehouse receipts only')
    expected_handle = costs.to_id.map(wh.handling_cost_per_unit).fillna(0)
    test('handling_destination_lanes',bool(np.allclose(costs.handling_cost,expected_handle,rtol=0,atol=1e-8)),'destination handling charged on every warehouse receipt')
    demand = S['customer_demand'].groupby('product_id').annual_demand.sum()
    capacity = pd.concat([S['vendor_capacity'],S['plant_capacity']]).groupby('product_id').capacity.sum()
    test('supply_slack',bool((capacity>demand).all()),(capacity/demand).to_dict())
    test('every_customer_positive',bool((S['customer_demand'].groupby('customer_id').annual_demand.sum()>0).all()),394)
    if 'pallets_per_annual_unit' in pp:
        expected_coefficient = pp.replenishment_cycle_days/(730*pp.units_per_pallet)
        test('stock_conversion',bool(np.allclose(pp.pallets_per_annual_unit,expected_coefficient,atol=1e-14,rtol=0)),expected_coefficient.to_dict())
        needed = float((demand*pp.pallets_per_annual_unit).sum())
        test('franklin_can_absorb_all_inventory',wh.at['W1','capacity_pallets']>=needed,needed)
        expected_hold = pp.annual_holding_cost_per_unit*pp.replenishment_cycle_days/730
        test('cycle_holding_conversion',bool(np.allclose(pp.holding_cost_per_unit,expected_hold,atol=1e-12,rtol=0)),expected_hold.to_dict())
    test('fixed_cost_inputs',set(S['warehouse_expansion_costs'].warehouse_id)==set(WIDS) and len(S['warehouse_expansion_costs'])==4,'one fixed charge per warehouse')
    if any(c['result']=='FAIL' for c in checks):
        raise AssertionError(pd.DataFrame(checks).query("result=='FAIL'").to_string(index=False))
    return checks


def config_sweep(S, radius=1000.0):
    rows,results = [],{}
    for k in range(4):
        for extra in itertools.combinations(WIDS[1:],k):
            outbound = ['W1',*extra]
            r = solve(S,outbound,radius)
            label = ','.join(TAG[w] for w in outbound)
            results[label] = r
            row = dict(config=label,outbound=','.join(outbound),radius_miles=radius,
                       status='FEASIBLE' if r['status']==0 else 'INFEASIBLE',solver_message=r['message'],
                       solver=r['backend'],solver_status=r['status'],
                       lp_cost=r['objective'],exceptions=len(r['exceptions']),n_vars=r['n_vars'],n_cons=r['n_cons'])
            if r['status']==0:
                row.update(transport=r['transport'],holding=r['holding'],handling=r['handling'],
                           holding_and_handling=r['holding']+r['handling'],fixed_cost=r['fixed_cost'],
                           overall_cost=r['total_cost'],ww_units=r['ww_units'],eligible_wc_arcs=r['eligible_wc_arcs'])
                for w in WIDS:
                    row[w+'_utilization'] = r['pallets_used'][w]/r['capacity'][w]
            rows.append(row)
    df = pd.DataFrame(rows)
    base = df.overall_cost.iloc[0]
    if not np.isfinite(base):
        raise AssertionError('Franklin baseline infeasible: '+results['T']['message'])
    df['rel_overall'] = df.overall_cost/base
    df['rel_lp'] = df.lp_cost/df.lp_cost.iloc[0]
    df['savings_pct'] = 100*(1-df.rel_overall)
    return df,results


def radius_sweep(S, outbound=('W1','W2'), radii=(1000,1500,2000,2500,3000), collect=None):
    rows = []
    previous = None
    for radius in radii:
        r = solve(S,outbound,radius)
        if r['status']!=0:
            raise AssertionError(f'Infeasible radius experiment: {radius}: {r["message"]}')
        if collect is not None:
            collect.extend(dict(experiment=f'radius_{radius}',**c) for c in r['validation'])
        used = r['V']
        positive = used[used.flow>1e-5]
        eligible_pairs = set(zip(used.loc[used.lane_type=='W-C','from_id'],used.loc[used.lane_type=='W-C','to_id']))
        if previous is not None:
            assert previous[0] <= eligible_pairs, 'Radius arc sets must be nested'
            assert r['objective'] <= previous[1]+.01, 'Radius relaxation increased cost'
        previous = eligible_pairs,r['objective']
        rows.append(dict(radius=radius,lp_cost=r['objective'],transport=r['transport'],holding=r['holding'],
                         handling=r['handling'],fixed_cost=r['fixed_cost'],overall_cost=r['total_cost'],
                         exceptions=len(r['exceptions']),eligible_wc_arcs=r['eligible_wc_arcs'],
                         used_wc_arcs=len(positive.loc[positive.lane_type=='W-C',['from_id','to_id']].drop_duplicates()),
                         ww_units=r['ww_units'],n_vars=r['n_vars'],status='FEASIBLE',solver=r['backend'],solver_status=r['status']))
    df = pd.DataFrame(rows)
    df['normalized_cost'] = df.lp_cost/df.lp_cost.iloc[0]
    df['savings_pct'] = 100*(1-df.normalized_cost)
    df['normalized_overall_cost'] = df.overall_cost/df.overall_cost.iloc[0]
    df['overall_savings_pct'] = 100*(1-df.normalized_overall_cost)
    return df


def with_rate_ratio(S, ratio):
    """Change ONLY outbound linehaul; retain noise, TL, ocean, handling, holding."""
    parameters = S['assumptions'].set_index('parameter').value
    base_ratio = float(parameters['LTL_rate'])/float(parameters['TL_rate'])
    out = dict(S)
    out['route_costs'] = S['route_costs'].copy()
    mask = out['route_costs'].lane_type=='W-C'
    out['route_costs'].loc[mask,'transport_cost'] *= ratio/base_ratio
    out['route_costs']['unit_cost'] = out['route_costs'][['transport_cost','holding_cost','handling_cost']].sum(axis=1)
    return out


def audit_archived_v2(validation):
    """Controlled experiments on the untouched pre-audit data, if available."""
    import re
    archives = sorted(p for p in Path('backups').glob('*')
                      if (p/'nukote_synthetic_data_v2.xlsx').exists() and (p/'nukote_results.xlsx').exists())
    if not archives:
        return {}, 'Archive unavailable; archived-data experiments were not executed.'
    archive = archives[0]
    legacy = load(archive/'nukote_synthetic_data_v2.xlsx')
    if 'lp_best_config_is_TC' not in set(legacy['checks'].name):
        return {}, 'Original calibrated v2 archive unavailable; archived experiments not executed.'
    numbers = legacy['checks'].set_index('name').at['LTL_rate_exceeds_TL','numbers']
    tl = float(re.search(r'base_TL=([\d.eE+-]+)',numbers).group(1))
    ltl = float(re.search(r'base_LTL=([\d.eE+-]+)',numbers).group(1))
    legacy['assumptions'] = pd.DataFrame([('TL_rate',tl),('LTL_rate',ltl)],columns=['parameter','value'])
    original = pd.read_excel(archive/'nukote_results.xlsx',sheet_name='configurations')
    rerun,runs = config_sweep(legacy)
    comparison = rerun[['config','lp_cost']].merge(original[['config','lp_cost']],on='config',suffixes=('_rerun','_archived'),validate='one_to_one')
    comparison['difference_usd'] = comparison.lp_cost_rerun-comparison.lp_cost_archived
    if len(comparison)!=8 or comparison.difference_usd.abs().max()>.01:
        raise AssertionError('Archived original LP objectives disagree with new solver')
    validation.append(dict(experiment='archived_v2',name='archived_objective_agreement',result='PASS',
                           detail=f'max_difference_USD={comparison.difference_usd.abs().max()}; tolerance=0.01'))
    for label,r in runs.items():
        validation.extend(dict(experiment='archived_config_'+label,**c) for c in r['validation'])
    local = []
    radius = radius_sweep(legacy,collect=local)
    saved_radius = pd.read_excel(archive/'nukote_results.xlsx',sheet_name='radius_sweep')
    radius_delta = float(np.max(np.abs(radius.lp_cost.to_numpy()-saved_radius.lp_cost.to_numpy())))
    if radius_delta>.01:
        raise AssertionError('Archived radius objectives disagree with new solver')
    validation.append(dict(experiment='archived_v2',name='archived_radius_objective_agreement',result='PASS',
                           detail=f'max_difference_USD={radius_delta}; tolerance=0.01'))
    validation.extend(dict(c,experiment='archived_'+c['experiment']) for c in local)
    ratio_results = []
    for ratio in np.round(np.arange(1,2.501,.25),2):
        local = []
        frame = radius_sweep(with_rate_ratio(legacy,ratio),collect=local)
        frame.insert(0,'rate_ratio',ratio)
        ratio_results.append(frame)
        validation.extend(dict(c,experiment=f'archived_ratio_{ratio}_'+c['experiment']) for c in local)
    print('Archived-data objectives reproduced and controlled ratio sweep executed.',flush=True)
    import hashlib
    provenance = pd.DataFrame([dict(file=(archive/name).as_posix(),
        sha256=hashlib.sha256((archive/name).read_bytes()).hexdigest(),
        role='Pre-audit calibrated synthetic v2; not historical Nu-kote observations')
        for name in ['generate_nukote_data_v2.py','nukote_synthetic_data_v2.xlsx','nukote_lp.py','nukote_results.xlsx']])
    return dict(archived_objective_comparison=comparison,archived_radius_sweep=radius,
                archived_rate_sensitivity=pd.concat(ratio_results,ignore_index=True),archive_provenance=provenance),str(archive)


def generator_reproducibility(S):
    import uuid
    import generate_nukote_data_v2 as generator
    checks = []
    directory = Path('.runtime_tmp')
    directory.mkdir(exist_ok=True)
    path = directory/('recreated_'+uuid.uuid4().hex+'.xlsx')
    try:
        generator.main(output=path)
        regenerated = load(path)
        if list(S)!=list(regenerated):
            raise AssertionError('Generator changed sheet names/order')
        for name in S:
            pd.testing.assert_frame_equal(S[name],regenerated[name],check_dtype=False,check_exact=True)
            checks.append(dict(experiment='reproducibility',name='exact_sheet_'+name,result='PASS',detail=f'{len(S[name])} rows; all cells compared'))
    finally:
        path.unlink(missing_ok=True)
    return checks


def solver_comparison(S, primary_results, validation):
    """Fresh cross-solver comparison; never replace a missing run with archived data."""
    try:
        from scipy.optimize import linprog  # noqa: F401
    except ImportError:
        return pd.DataFrame([dict(experiment='fresh_HiGHS_vs_NumPy',result='SKIP',
            reason='SciPy/HiGHS is not installed; no fresh solver comparison executed.',
            tolerance_usd='0.01 + 1e-9 * abs(HiGHS objective)')])
    rows = []
    # All eight configurations plus the five Franklin-Chatsworth radii.
    experiments = [(f'configuration_{k}',r['outbound'],1000,r) for k,r in primary_results.items()]
    experiments += [(f'radius_{r}', ['W1','W2'],r,None) for r in [1000,1500,2000,2500,3000]]
    for label,outbound,radius,reference in experiments:
        highs = reference if reference is not None and reference['backend']=='SciPy/HiGHS' else solve(S,outbound,radius,solver='highs')
        numpy_result = solve(S,outbound,radius,solver='numpy')
        if highs['status']!=0 or numpy_result['status']!=0:
            raise AssertionError('Cross-solver case is not feasible: '+label)
        tolerance = .01+1e-9*abs(highs['objective'])
        difference = abs(highs['objective']-numpy_result['objective'])
        if difference>tolerance:
            raise AssertionError(f'Cross-solver objective disagreement {label}: {difference}')
        rows.append(dict(experiment=label,result='PASS',highs_status=highs['status'],numpy_status=numpy_result['status'],
            highs_objective_usd=highs['objective'],numpy_objective_usd=numpy_result['objective'],
            absolute_difference_usd=difference,tolerance_usd=tolerance))
        for backend,r in [('highs',highs),('numpy',numpy_result)]:
            validation.extend(dict(experiment=f'comparison_{backend}_{label}',**c) for c in r['validation'])
    return pd.DataFrame(rows)


def network_diagnostics(S, results, validation):
    """Objective-neutral transfer test and small, clearly labeled capacity perturbations."""
    import copy
    no_transfers = dict(S)
    no_transfers['distances'] = S['distances'].query("lane_type != 'W-W'").copy()
    no_transfers['route_costs'] = S['route_costs'].query("lane_type != 'W-W'").copy()
    rows = []
    for label,baseline in results.items():
        alternative = solve(no_transfers,baseline['outbound'],baseline['radius'])
        if alternative['status']!=0:
            raise AssertionError('No-transfer diagnostic infeasible: '+label)
        difference = alternative['objective']-baseline['objective']
        if difference<-.01:
            raise AssertionError('Removing transfer arcs improved objective beyond tolerance')
        rows.append(dict(config=label,lp_cost=baseline['objective'],lp_cost_no_transfers=alternative['objective'],
            difference_usd=difference,transfer_units_raw=baseline['ww_units'],
            transfer_units_above_flow_tolerance=float(baseline['V'].loc[(baseline['V'].lane_type=='W-W')&(baseline['V'].flow>1e-5),'flow'].sum()),
            transfers_unnecessary=abs(difference)<=.01))
        validation.extend(dict(experiment='no_transfers_'+label,**c) for c in alternative['validation'])
    costs = S['route_costs'].set_index(['from_id','to_id','product_id']).unit_cost
    source = pd.concat([S['vendor_capacity'].rename(columns={'vendor_id':'src'}),S['plant_capacity'].rename(columns={'plant_id':'src'})])
    dominance = []
    for row in source[source.capacity>0].itertuples(index=False):
        for first in WIDS:
            for destination in WIDS:
                if first!=destination:
                    extra = costs[row.src,first,row.product_id]+costs[first,destination,row.product_id]-costs[row.src,destination,row.product_id]
                    dominance.append(dict(source_id=row.src,product_id=row.product_id,via_warehouse=first,
                        final_warehouse=destination,extra_cost_per_unit=float(extra),direct_no_more_expensive=extra>=-1e-8))
    best = min(results,key=lambda k:results[k]['total_cost'])
    base = results[best]
    perturbations = []
    for w in WIDS:
        changed = copy.deepcopy(S)
        delta = .01
        mask = changed['warehouse_capacity'].warehouse_id==w
        changed['warehouse_capacity']['capacity_pallets'] = changed['warehouse_capacity'].capacity_pallets.astype(float)
        changed['warehouse_capacity'].loc[mask,'capacity_pallets'] += delta
        r = solve(changed,base['outbound'],base['radius'])
        if r['status']!=0 or r['objective']>base['objective']+.01:
            raise AssertionError('Capacity relaxation failed: '+w)
        perturbations.append(dict(config=best,warehouse=w,added_pallet_positions=delta,
            original_lp_cost=base['objective'],relaxed_lp_cost=r['objective'],savings_usd=base['objective']-r['objective'],
            dual_predicted_savings_usd=-base['pallet_dual'][w]*delta,
            original_utilization=base['pallets_used'][w]/base['capacity'][w]))
        validation.extend(dict(experiment='capacity_perturbation_'+w,**c) for c in r['validation'])
    return dict(network_diagnostics=pd.DataFrame(rows),transfer_dominance=pd.DataFrame(dominance),
                capacity_perturbations=pd.DataFrame(perturbations))


def self_tests(solver='auto'):
    """Small analytical tests use the exact same solver builder as full runs."""
    locations = pd.DataFrame([('V1','vendor'),('V2','vendor')]+[(w,'warehouse') for w in WIDS]+[('C001','customer')],columns=['id','type'])
    products = pd.DataFrame([('1',1,1)],columns=['product_id','units_per_pallet','pallets_per_annual_unit'])
    distances, costs = [],[]
    for src in ['V1','V2']:
        for i,w in enumerate(WIDS):
            t = i if i<2 else 20
            t += 5 if src=='V2' else 0
            distances.append((src,w,'V-W',100))
            costs.append((src,w,'V-W','1',t,0,0,t))
    for a in WIDS:
        for b in WIDS:
            if a!=b:
                distances.append((a,b,'W-W',100))
                costs.append((a,b,'W-W','1',20,0,0,20))
    for i,w in enumerate(WIDS):
        distances.append((w,'C001','W-C',100+100*i))
        t = i+1 if i<2 else 20
        costs.append((w,'C001','W-C','1',t,0,0,t))
    S = dict(locations=locations,products=products,
        customer_demand=pd.DataFrame([('C001','1',10)],columns=['customer_id','product_id','annual_demand']),
        vendor_capacity=pd.DataFrame([('V1','1',15),('V2','1',20)],columns=['vendor_id','product_id','capacity']),
        plant_capacity=pd.DataFrame(columns=['plant_id','product_id','capacity']),
        warehouse_capacity=pd.DataFrame([(w,6 if w=='W1' else 10) for w in WIDS],columns=['warehouse_id','capacity_pallets']),
        warehouse_expansion_costs=pd.DataFrame([(w,5 if w=='W2' else 0) for w in WIDS],columns=['warehouse_id','annual_fixed_cost_usd']),
        distances=pd.DataFrame(distances,columns=['from_id','to_id','lane_type','miles']),
        route_costs=pd.DataFrame(costs,columns=['from_id','to_id','lane_type','product_id','transport_cost','holding_cost','handling_cost','unit_cost']))
    checks = []
    def record(name, condition, detail):
        if not condition:
            raise AssertionError(name+': '+str(detail))
        checks.append(dict(experiment='analytical_tests',name=name,result='PASS',detail=str(detail)))
    r = solve(S,['W1','W2'],solver=solver)
    record('binding_capacity_known_optimum',abs(r['objective']-18)<1e-7 and abs(r['pallets_used']['W1']-6)<1e-7,r['objective'])
    record('fixed_charge_known_total',abs(r['total_cost']-23)<1e-7,r['total_cost'])
    source = r['V'].query("lane_type=='V-W'").groupby('from_id').flow.sum()
    record('cheaper_source_with_unused_capacity',abs(source['V1']-10)<1e-7 and abs(source['V2'])<1e-7,source.to_dict())
    checks.extend(dict(experiment='analytical_binding',**c) for c in r['validation'])
    infeasible = solve(S,['W1'],solver=solver)
    record('documented_infeasibility',infeasible['status']==2,'W1 storage 6 < demand inventory 10')
    S['warehouse_capacity'].loc[S['warehouse_capacity'].warehouse_id=='W1','capacity_pallets'] = 10
    exception = solve(S,['W1','W2'],radius=50,solver=solver)
    record('exception_nearest_known_optimum',exception['exceptions']==['C001'] and abs(exception['objective']-10)<1e-7,exception['objective'])
    checks.extend(dict(experiment='analytical_exception',**c) for c in exception['validation'])
    # Deliberately corrupt a feasible flow and ensure the independent checker rejects it.
    broken = dict(exception)
    broken['V'] = exception['V'].copy()
    index = broken['V'].query("lane_type=='W-C'").index[0]
    broken['V'].loc[index,'flow'] += 1
    rejected = False
    try:
        validate_solution(S,broken)
    except AssertionError:
        rejected = True
    record('checker_rejects_corrupted_flow',rejected,'demand/flow/objective corruption detected')
    import copy
    multi = copy.deepcopy(S)
    multi['warehouse_capacity'].loc[multi['warehouse_capacity'].warehouse_id=='W1','capacity_pallets'] = 6
    multi['products'] = pd.DataFrame([('1',1,1),('2',2,.5)],columns=['product_id','units_per_pallet','pallets_per_annual_unit'])
    for name in ['customer_demand','vendor_capacity','plant_capacity','route_costs']:
        extra = multi[name].copy()
        extra['product_id'] = '2'
        multi[name] = pd.concat([multi[name],extra],ignore_index=True)
    mixed = solve(multi,['W1','W2'],solver=solver)
    record('mixed_product_pallet_conversion',abs(mixed['objective']-38)<1e-7 and abs(mixed['pallets_used']['W1']-6)<1e-7,mixed['objective'])
    checks.extend(dict(experiment='analytical_mixed_pallets',**c) for c in mixed['validation'])
    for label in ['source_capacity','storage_capacity','fixed_fee','objective','prohibited_route']:
        corrupted_inputs = copy.deepcopy(S)
        corrupted = dict(exception)
        corrupted['V'] = exception['V'].copy()
        if label=='source_capacity':
            corrupted_inputs['vendor_capacity'].loc[0,'capacity'] = 9
        elif label=='storage_capacity':
            corrupted_inputs['warehouse_capacity'].loc[0,'capacity_pallets'] = 9
        elif label=='fixed_fee':
            corrupted['fixed_cost'] += 5
        elif label=='objective':
            corrupted['objective'] += 1
        else:
            i = corrupted['V'].query("lane_type=='W-C'").index[0]
            corrupted['V'].loc[i,['from_id','to_id','lane_type']] = ['W1','W1','W-W']
        rejected = False
        try:
            validate_solution(corrupted_inputs,corrupted)
        except AssertionError:
            rejected = True
        record('checker_rejects_'+label,rejected,'intentional corruption rejected')
    return checks


def figures(cfg, rad, sensitivity, outdir, archived=None, run_id='', workbook_sha256='', paper_reference=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    outdir = Path(outdir)
    from matplotlib.patches import Patch, Rectangle
    metadata = dict(RunID=run_id,SourceWorkbookSHA256=workbook_sha256)
    def save(fig,name):
        fig.savefig(outdir/name,dpi=190,metadata=metadata)
        plt.close(fig)
    components = [('transport','Transportation','#377eb8'),('holding','Holding','#ffab40'),
                  ('handling','Handling','#80b89c'),('fixed_cost','Expansion fixed cost','#9775bd')]
    if not np.allclose(cfg[[c[0] for c in components]].sum(axis=1),cfg.overall_cost,rtol=0,atol=.01):
        raise AssertionError('Figure 2 components do not sum to total annual cost')
    fig,ax = plt.subplots(figsize=(12,6.3))
    positions = np.arange(len(cfg)); bottom = np.zeros(len(cfg))
    best = int(np.argmin(cfg.overall_cost.to_numpy()))
    for key,label,color in components:
        heights = cfg[key].to_numpy()/1e6
        bars = ax.bar(positions,heights,bottom=bottom,label=label,color=color,edgecolor='white',linewidth=.5)
        bars[0].set_hatch('///'); bars[0].set_edgecolor('#303030')
        bottom += heights
    ax.add_patch(Rectangle((best-.4,0),.8,bottom[best],fill=False,edgecolor='#147d32',linewidth=2.8))
    for i,(total,relative) in enumerate(zip(bottom,cfg.rel_overall)):
        note = '\nBaseline' if i==0 else ('\nLowest total' if i==best else '')
        ax.text(i,total+.045,f'${total:.3f}m\n{relative:.3f} x baseline'+note,
                ha='center',va='bottom',fontsize=8,color='#147d32' if i==best else '#222222')
    ax.set_xticks(positions, cfg.config)
    ax.set_ylim(0,float(max(bottom))*1.25)
    ax.set_ylabel('Total annual cost (million USD; components stacked)')
    ax.set_xlabel('Outbound warehouse configuration')
    ax.grid(axis='y',alpha=.2); ax.set_axisbelow(True)
    handles = [Patch(facecolor=color,label=label) for _,label,color in components]
    labels = [label for _,label,_ in components]
    handles += [Patch(facecolor='white',edgecolor='#303030',hatch='///'),Patch(facecolor='white',edgecolor='#147d32',linewidth=2)]
    labels += ['Franklin-only baseline','Lowest-cost configuration']
    ax.legend(handles,labels,ncol=3,fontsize=9,loc='upper center')
    fig.suptitle('Synthetic Nu-kote LP: annual cost components at a 1,000-mile radius',fontsize=13)
    fig.text(.5,.025,'T = Franklin   |   C = Chatsworth   |   N = Rochester   |   P = Connellsville',ha='center',fontsize=10)
    fig.tight_layout(rect=[0,.05,1,.95]); save(fig,'fig2_configurations.png')
    fig,axes = plt.subplots(1,2,figsize=(12,4.8))
    axes[0].plot(rad.radius,rad.normalized_cost,'o-',label='LP cost (excludes expansion fixed cost)')
    axes[0].plot(rad.radius,rad.normalized_overall_cost,'s--',label='LP + expansion fixed cost')
    if paper_reference is None:
        raise ValueError('Figure 3 requires the documented five-point paper reference')
    axes[0].plot(paper_reference.radius_miles,paper_reference.normalized_reference,'D--',color='#555555',
                 label='Paper Fig. 3: approximate visual interpretation')
    axes[0].set_ylim(0,1.08)
    axes[0].set_ylabel('Cost / cost at 1,000 miles')
    axes[1].plot(rad.radius,rad.savings_pct,'o-',label='Synthetic LP savings')
    axes[1].plot(paper_reference.radius_miles,100*(1-paper_reference.normalized_reference),'D--',color='#555555',
                 label='Paper: approximate reference savings')
    axes[1].annotate(f'{rad.savings_pct.iloc[-1]:.3f}% synthetic saving',
        (rad.radius.iloc[-1],rad.savings_pct.iloc[-1]),xytext=(-8,12),textcoords='offset points',ha='right',fontsize=9)
    axes[1].set_ylim(bottom=0)
    axes[1].set_ylabel('LP savings versus 1,000 miles (%)')
    for ax in axes:
        ax.set_xlabel('Allowable shipping radius (miles)'); ax.grid(alpha=.2); ax.legend(fontsize=8)
    fig.suptitle('Franklin + Chatsworth: radius sensitivity (synthetic inputs)')
    fig.text(.5,.015,'Paper values (1.00, 0.90, 0.82, 0.82, 0.82) are approximate readings of p. 145, not exact published data.',
             ha='center',fontsize=8)
    fig.tight_layout(rect=[0,.04,1,.95]); save(fig,'fig3_radius.png')
    def rate_figure(frame,title,name):
        fig,axes = plt.subplots(1,2,figsize=(12,5.8))
        for ratio,group in frame.groupby('rate_ratio'):
            axes[0].plot(group.radius,group.normalized_cost,'o-',label=f'{ratio:.2f}')
            axes[1].plot(group.radius,group.savings_pct,'o-')
        lower = max(0,float(frame.normalized_cost.min())-.01)
        axes[0].set_ylim(lower,1.005); axes[0].set_ylabel('LP cost / own 1,000-mile LP cost')
        axes[1].set_ylim(0,max(.1,float(frame.savings_pct.max())*1.12))
        axes[1].set_ylabel('Savings versus own 1,000-mile LP cost (%)')
        for ax in axes:
            ax.set_xticks([1000,1500,2000,2500,3000])
            ax.set_xlabel('Allowable shipping radius (miles)'); ax.grid(alpha=.2)
        h,l = axes[0].get_legend_handles_labels()
        fig.legend(h,l,loc='upper center',bbox_to_anchor=(.5,.91),ncol=7,title='Nominal LTL/TL base rate ratio',fontsize=9)
        fig.suptitle(title,fontsize=12)
        fig.text(.5,.04,'Ratio = outbound LTL base USD/(lb-mile) / inbound TL base USD/(lb-mile). Only outbound linehaul changes.',
                 ha='center',fontsize=8)
        fig.text(.5,.015,f'Normalized-cost axis truncated to [{lower:.3f}, 1.005] for detail; savings axis starts at zero. Fixed fees excluded.',
                 ha='center',fontsize=8)
        fig.tight_layout(rect=[0,.09,1,.79]); save(fig,name)
    rate_figure(sensitivity,'Synthetic inputs: freight-rate sensitivity, Franklin + Chatsworth','fig4_rate_ratio.png')
    if archived and 'archived_rate_sensitivity' in archived:
        rate_figure(archived['archived_rate_sensitivity'],
            'Appendix: archived pre-audit v2 synthetic inputs (not historical validation)','figA1_archived_rate_ratio.png')


def main(path='nukote_synthetic_data_v2.xlsx', outdir='.'):
    import hashlib
    import platform
    import uuid
    run_id = str(uuid.uuid4())
    try:
        import scipy
        scipy_version = scipy.__version__
    except ImportError:
        scipy_version = 'unavailable; NumPy primal-dual fallback executed'
    outdir = Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    S = load(path)
    validation = [dict(experiment='inputs',**c) for c in input_checks(S)]
    validation.extend(generator_reproducibility(S))
    validation.extend(self_tests())
    print('Input and analytical solver tests passed.',flush=True)
    archived_tables,archive_path = audit_archived_v2(validation)
    cfg,results = config_sweep(S)
    if not (cfg.status=='FEASIBLE').all():
        raise AssertionError('Final acceptance requires all eight configurations feasible; see solver status')
    for label,r in results.items():
        if r['status']==0:
            validation.extend(dict(experiment='configuration_'+label,**c) for c in r['validation'])
        else:
            validation.append(dict(experiment='configuration_'+label,name='feasibility',result='WARN',detail=r['message']))
    comparison = solver_comparison(S,results,validation)
    for row in comparison.to_dict('records'):
        validation.append(dict(experiment='cross_solver',name=row['experiment'],result=row['result'],
                               detail=str(row)))
    if (comparison.result=='PASS').any():
        validation.extend(dict(c,experiment='numpy_'+c['experiment']) for c in self_tests(solver='numpy'))
    diagnostics = network_diagnostics(S,results,validation)
    component_error = float(np.max(np.abs(cfg[['transport','holding','handling','fixed_cost']].sum(axis=1)-cfg.overall_cost)))
    if component_error>.01:
        raise AssertionError('Configuration components disagree with objective')
    validation.append(dict(experiment='configurations',name='component_totals',result='PASS',detail=f'max_difference_usd={component_error}'))
    rad = radius_sweep(S,collect=validation)
    validation.append(dict(experiment='radius',name='nonincreasing_cost_and_nested_arcs',result='PASS',detail='Asserted at every radius in every sweep; absolute cost tolerance USD 0.01'))
    sensitivities,freight_configs = [],[]
    ratios = np.round(np.arange(1,2.501,.25),2)
    for ratio in ratios:
        scenario = with_rate_ratio(S,float(ratio))
        local_checks = []
        sensitivity = radius_sweep(scenario,collect=local_checks)
        sensitivity.insert(0,'rate_ratio',ratio)
        sensitivities.append(sensitivity)
        validation.extend(dict(c,experiment=f'ratio_{ratio}_'+c['experiment']) for c in local_checks)
        config, runs = config_sweep(scenario)
        config.insert(0,'rate_ratio',ratio)
        freight_configs.append(config)
        for label,r in runs.items():
            if r['status']==0:
                validation.extend(dict(experiment=f'freight_config_{ratio}_{label}',**c) for c in r['validation'])
            else:
                validation.append(dict(experiment=f'freight_config_{ratio}_{label}',name='feasibility',result='WARN',detail=r['message']))
        print(f'Executed ratio {ratio:.2f}: radius savings {sensitivity.savings_pct.iloc[-1]:.4f}%',flush=True)
    sensitivity = pd.concat(sensitivities,ignore_index=True)
    freight_config = pd.concat(freight_configs,ignore_index=True)
    fixed_rows = []
    for multiplier in [0,.5,1,1.5,2]:
        for row in cfg.to_dict('records'):
            fixed_rows.append(dict(config=row['config'],fixed_cost_multiplier=multiplier,
                lp_cost=row['lp_cost'],fixed_cost=row.get('fixed_cost',np.nan)*multiplier,
                overall_cost=row['lp_cost']+row.get('fixed_cost',np.nan)*multiplier,status=row['status']))
    fixed_sensitivity = pd.DataFrame(fixed_rows)
    best = cfg.loc[cfg.overall_cost.idxmin(),'config']
    b = results[best]
    utilization = pd.DataFrame([dict(config=label,warehouse=w,outbound=w in r['outbound'],
        average_pallet_positions=r['pallets_used'][w],capacity_pallet_positions=r['capacity'][w],
        utilization=r['pallets_used'][w]/r['capacity'][w],shadow_price_usd_per_position=r['pallet_dual'][w])
        for label,r in results.items() if r['status']==0 for w in WIDS])
    warnings = [dict(name='synthetic_only',detail='No historical freight observations; no reproduction of historical 5% validation or Excel Solver.'),
                dict(name='exception_policy',detail='Nearest active outbound warehouse if none within radius; paper does not specify the assignment rule.'),
                dict(name='inventory_assumption',detail='15-day cycle stock; no safety stock, cube, seasonality or shipment economies of scale.'),
                dict(name='paper_discrepancy',detail=f'Paper nearly 18%; synthetic LP endpoint savings {rad.savings_pct.iloc[-1]:.6f}%.')]
    if b['backend']!='SciPy/HiGHS':
        warnings.append(dict(name='solver_backend',detail='NumPy primal-dual fallback executed; fresh HiGHS comparison SKIP because SciPy is not installed. Archived comparisons are separate regression checks.'))
    paper_reference = pd.DataFrame(dict(radius_miles=[1000,1500,2000,2500,3000],normalized_reference=[1.,.90,.82,.82,.82],
        provenance='Approximate visual readings of supplied PDF p.145 Figure 3; not exact published observations'))
    metadata = pd.DataFrame([
        ('input_sha256',hashlib.sha256(Path(path).read_bytes()).hexdigest()),
        ('run_id',run_id),
        ('generator_sha256',hashlib.sha256(Path('generate_nukote_data_v2.py').read_bytes()).hexdigest()),
        ('lp_script_sha256',hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        ('python',platform.python_version()),('numpy',np.__version__),('pandas',pd.__version__),('scipy',scipy_version),
        ('solver',b['backend']),('flow_abs_tolerance','1e-5'),
        ('archive_path',archive_path),
        ('objective_tolerance_usd','0.01 + 1e-9 * abs(objective)'),
        ('radius_normalization','LP / LP at 1000 miles, same configuration and rate ratio'),
        ('config_normalization','(LP + fixed) / Franklin-only (LP + fixed), radius 1000'),
    ],columns=['parameter','value'])
    summary = pd.DataFrame([
        ('Scope','Synthetic educational dataset; not original Nu-kote observations'),
        ('Run ID',run_id),('Primary solver',b['backend']),('Solver status',b['message']),
        ('Feasibility','All 8 Franklin-retaining configurations feasible'),('Best configuration',best),
        ('Best annual total USD',b['total_cost']),('Savings vs Franklin-only %',float(cfg.loc[cfg.config==best,'savings_pct'].iloc[0])),
        ('Radius savings % (LP only)',float(rad.savings_pct.iloc[-1])),
        ('Cross-solver comparison','PASS' if (comparison.result=='PASS').all() else 'SKIP: SciPy/HiGHS not installed'),
        ('Generator validation','89 PASS; 0 FAIL'),
        ('Model checks passed',sum(c['result']=='PASS' for c in validation)),
        ('Model checks failed',sum(c['result']=='FAIL' for c in validation)),
        ('Model checks skipped',sum(c['result']=='SKIP' for c in validation)),
        ('Numerical tolerances','Flow/storage: 1e-5. Cost: USD 0.01 + 1e-9 * abs(LP objective).'),
        ('Units','Flow: units/year; costs: USD/year; warehouse capacity: average-stock pallet positions'),
        ('Normalization','Configurations: total / Franklin total. Radius: LP / own 1000-mile LP, same ratio/configuration.'),
        ('Transfers','Raw numerical residuals are retained; meaningful flow threshold is 1e-5 units/year'),
        ('Sheet guide','configurations; radius_sweep; rate_ratio_sensitivity; network_diagnostics; capacity_perturbations; solver_comparison; validation_summary'),
        ('Archived sheets','Pre-audit synthetic inputs only; source paths and SHA-256 in archive_provenance'),
    ],columns=['item','value'])
    tables = dict(README=summary,configurations=cfg,radius_sweep=rad,rate_ratio_sensitivity=sensitivity,
        freight_config_sensitivity=freight_config,fixed_cost_sensitivity=fixed_sensitivity,
        warehouse_utilization=utilization,cost_breakdown=cfg[['config','transport','holding','handling','fixed_cost','overall_cost']],
        supply_shadow_prices=b['supply_dual'],best_flows=b['V'],
        solver_comparison=comparison,paper_reference=paper_reference,
        validation_checks=pd.DataFrame(validation),warnings=pd.DataFrame(warnings),run_metadata=metadata)
    tables['solver_certificates'] = pd.DataFrame([dict(config=k,backend=r['backend'],solver_status=r['status'],
        objective=r['objective'],dual_objective=r['dual_objective'],duality_gap_usd=r['duality_gap_usd'],
        dual_max_inequality_marginal=r['dual_max_inequality_marginal'],min_reduced_cost=r['min_reduced_cost'],
        certificate_dual_residual=r['certificate_dual_residual']) for k,r in results.items()])
    tables.update(archived_tables)
    tables.update(diagnostics)
    detail_summary = pd.DataFrame(validation).groupby(['experiment','result']).size().reset_index(name='checks')
    totals = pd.DataFrame(validation).groupby('result').size().reset_index(name='checks')
    totals.insert(0,'experiment','ALL_CHECKS')
    tables['validation_summary'] = pd.concat([totals,detail_summary],ignore_index=True)
    for name,frame in S.items():
        tables[('input_'+name)[:31]] = frame
    with pd.ExcelWriter(outdir/'nukote_results.xlsx',engine='openpyxl') as writer:
        for name,df in tables.items():
            df.to_excel(writer,sheet_name=name,index=False)
            ws = writer.sheets[name]; ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
            for col in ws.columns:
                header = str(col[0].value)
                from openpyxl.styles import Alignment, Font, PatternFill
                text_width = max((len(str(cell.value)) for cell in col[:250] if isinstance(cell.value,str)),default=len(header))
                width = min(65,max(15,text_width+2))
                if header in ['detail','numbers','reason','rationale']:
                    width = 85
                if name=='README' and header=='value':
                    width = 105
                ws.column_dimensions[col[0].column_letter].width=width
                col[0].font=Font(bold=True,color='FFFFFF')
                col[0].fill=PatternFill('solid',fgColor='245475')
                col[0].alignment=Alignment(wrap_text=True,vertical='center')
                ws.row_dimensions[1].height=30
                for cell in col[1:]:
                    if isinstance(cell.value,float):
                        cell.number_format = '0.0000000000' if name.startswith('input_') else ('0.000000' if any(x in header for x in ['rel_','normalized','utilization','ratio','pallets_per_']) else '#,##0.0000')
                    if isinstance(cell.value,str) and header in ['detail','numbers','reason','rationale','value']:
                        cell.alignment=Alignment(wrap_text=True,vertical='top')
                        lines = max(1,int(np.ceil(len(cell.value)/width)))
                        ws.row_dimensions[cell.row].height=max(ws.row_dimensions[cell.row].height or 15,min(75,15*lines))
    # Figures consume the exported numbers, ensuring plotting/export agreement.
    exported = pd.read_excel(outdir/'nukote_results.xlsx',sheet_name=['configurations','radius_sweep','rate_ratio_sensitivity','paper_reference',*archived_tables])
    for key in exported:
        pd.testing.assert_frame_equal(tables[key],exported[key],check_dtype=False,rtol=1e-12,atol=1e-8)
    figures(exported['configurations'],exported['radius_sweep'],exported['rate_ratio_sensitivity'],outdir,exported,
            run_id,hashlib.sha256((outdir/'nukote_results.xlsx').read_bytes()).hexdigest(),exported['paper_reference'])
    report = build_report(cfg,rad,sensitivity,freight_config,fixed_sensitivity,validation,S,b['backend'],archived_tables,comparison,diagnostics)
    (outdir/'MODEL_VALIDATION.md').write_text(report,encoding='utf-8')
    print(cfg[['config','status','overall_cost','rel_overall','savings_pct']].to_string(index=False))
    print(f'Best configuration: {best}; annual total USD {b["total_cost"]:,.2f}')
    print(rad[['radius','lp_cost','overall_cost','normalized_cost','savings_pct','eligible_wc_arcs','exceptions']].to_string(index=False))
    print(f'Validation: {len(validation)} checks; failures={sum(c["result"]=="FAIL" for c in validation)}; feasibility warnings={sum(c["result"]=="WARN" for c in validation)}')
    print(f'Limitations/advisories: {len(warnings)} (see warnings sheet).')
    print('Fresh solver comparison: '+', '.join(comparison.result.tolist()))
    return cfg,rad,results


def build_report(cfg,rad,sensitivity,freight_configs,fixed_sensitivity,validation,S,backend,archived,comparison,diagnostics):
    best = cfg.loc[cfg.overall_cost.idxmin()]
    freight_best = freight_configs.loc[freight_configs.groupby('rate_ratio').overall_cost.idxmin()]
    fixed_best = fixed_sensitivity.loc[fixed_sensitivity.groupby('fixed_cost_multiplier').overall_cost.idxmin()]
    endpoint = sensitivity.query('radius == 3000')[['rate_ratio','savings_pct']].rename(columns={'savings_pct':'corrected_savings_pct'})
    archive_summary = 'Archived-data experiments were not executed: original backup unavailable.'
    if archived:
        original = archived['archived_rate_sensitivity'].query('radius == 3000')[['rate_ratio','savings_pct']]
        endpoint = endpoint.merge(original.rename(columns={'savings_pct':'original_v2_savings_pct'}),on='rate_ratio')
        delta = archived['archived_objective_comparison'].difference_usd.abs().max()
        archive_summary = f'All eight archived v2 objectives were reproduced within USD {delta:.9f}. This is agreement with saved outputs, not a fresh independent HiGHS execution. The original 0.5070167% radius saving was also reproduced.'
    def table(frame,columns):
        lines = ['| '+' | '.join(columns)+' |','| '+' | '.join(['---']*len(columns))+' |']
        for row in frame[columns].itertuples(index=False,name=None):
            cells = [f'{v:,.6f}' if isinstance(v,(float,np.floating)) else str(v) for v in row]
            lines.append('| '+' | '.join(cells)+' |')
        return '\n'.join(lines)
    p = S['products'].set_index('product_id')
    d = S['customer_demand'].groupby('product_id').annual_demand.sum()
    inventory = float((d*p.pallets_per_annual_unit).sum())
    base_rates = S['assumptions'].set_index('parameter').value
    ratio = float(base_rates['LTL_rate'])/float(base_rates['TL_rate'])
    if (comparison.result=='PASS').all():
        cross_solver_text = f'Fresh HiGHS vs NumPy comparison: PASS on {len(comparison)} cases (all eight configurations and five radii). Maximum objective difference USD {comparison.absolute_difference_usd.max():.9f}; tolerance USD 0.01 + 1e-9*abs(HiGHS objective). Both solutions passed independent feasibility/duality checks. HiGHS was the primary solver.'
    else:
        cross_solver_text = 'Fresh HiGHS vs NumPy comparison: SKIP. SciPy/HiGHS is not installed in the execution environment and package installation was unavailable. No fresh HiGHS solve or independent cross-solver agreement is claimed. The NumPy fallback and its analytical/primal-dual tests were executed.'
    dominance = diagnostics['transfer_dominance']
    direct_text = ('All tested eligible source/product two-leg detours are strictly more costly than the direct inbound lane.'
        if (dominance.extra_cost_per_unit>0).all() else 'Some detours may be individually competitive; the no-transfer re-solves quantify their actual network value.')
    perturb = diagnostics['capacity_perturbations']
    w4 = perturb.set_index('warehouse').loc['W4']
    w1 = perturb.set_index('warehouse').loc['W1']
    return f'''# Nu-kote synthetic LP: audit and validation

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
Base nominal LTL/TL ratio is {ratio:.6f}. Independent W2-W4 annual expansion fees
are USD 150,000-450,000 rounded to USD 1,000; W1 has zero incremental fee.

The **synthetic 15-day replenishment cycle** gives average stock = annual receipts
* 15/730. Holding per annual unit = cost_of_capital * standard_cost * 15/730;
physical pallet positions per annual unit a[p] = 15/(730 * units_per_pallet[p]).
Using the same fraction for holding and storage fixes the stock/throughput unit
mismatch. The paper states annual r*c per inventory unit but does not specify this
15-day conversion. Single-pass annual throughput is 3,815.0881 pallet equivalents,
whereas average inventory is {inventory:.4f} physical pallet positions. Units per
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

Backend actually used: **{backend}**. SciPy/HiGHS remains preferred when installed.
The existing NumPy Mehrotra fallback is retained; it eliminates the diagonal demand
block, solves a core system of at most 88 rows, requires primal/dual convergence
and a sub-cent gap, and recovers dual multipliers for eliminated zero-demand/source
variables. Infeasibility requires an empty-equality or Farkas certificate.

{cross_solver_text}

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

**{len(validation)} checks: {sum(c['result']=='PASS' for c in validation)} PASS,
{sum(c['result']=='FAIL' for c in validation)} FAIL,
{sum(c['result']=='WARN' for c in validation)} feasibility WARN,
{sum(c['result']=='SKIP' for c in validation)} SKIP.** Input generation
has a separate 89-check suite. {archive_summary}

## Current results

All eight configurations are feasible. **Best: {best['config']}**
(T=Franklin, C=Chatsworth, N=Rochester, P=Connellsville),
**USD {best['overall_cost']:,.2f}/year**, saving {best['savings_pct']:.4f}% versus W1 only.
The workbook gives transportation, holding, handling, fees and utilization separately.

{table(cfg,['config','overall_cost','fixed_cost','rel_overall','savings_pct'])}

## Why transfers vanish and capacity binds at Connellsville

The formulation permits all 12 W-W lanes; zero transfer flow is not an eligibility
bug. {direct_text} Among {len(dominance)} eligible source/product detours, the minimum
extra receipt-plus-freight cost is USD {dominance.extra_cost_per_unit.min():.6f}/unit.
Direct bypass also removes the intermediate warehouse's holding, handling and
storage use. Source/product output and the final warehouse's receipts stay the same.
No consolidation discount, manufacturing-input dependency or mandatory factory
receipt is imposed, so an intermediate stop need not have any economic purpose.
Fixing all W-W flows to zero and re-solving all eight configurations changed the
objective by at most USD {diagnostics['network_diagnostics'].difference_usd.abs().max():.9f}.
Raw numerical transfer residuals are retained, not represented as physical shipments;
meaningful flow is defined using the documented 1e-5-unit tolerance.

Franklin-only utilization is {100*cfg.iloc[0]['W1_utilization']:.3f}% because synthetic
Franklin capacity was deliberately drawn with spare stock capacity, independently
of solutions. In the best configuration W1 utilization is {100*best['W1_utilization']:.3f}%
and W4 is {100*best['W4_utilization']:.3f}%. The eastern customer geography, W4's
lower handling cost and its independently drawn 34 pallet positions make W4's
capacity valuable. Adding 0.01 position at W4 reduces LP cost by USD {w4.savings_usd:.6f}
(dual prediction USD {w4.dual_predicted_savings_usd:.6f}); the same increase at W1
saves USD {w1.savings_usd:.9f} (numerical zero within the USD 0.01 tolerance).
These are diagnostic perturbations only; original
capacities and final results were retained. The paper uses different confidential
inputs, so the warehouse that binds need not be the same. Detailed evidence is in
network_diagnostics, transfer_dominance and capacity_perturbations.

Franklin + Chatsworth radius sweep; LP normalization excludes the constant fixed
fee. Actual fixed-inclusive dollars and their own normalization are also exported.

{table(rad,['radius','lp_cost','normalized_cost','savings_pct','eligible_wc_arcs','exceptions'])}

Savings are **{rad.savings_pct.iloc[-1]:.6f}%**, leaving a
**{18-rad.savings_pct.iloc[-1]:.6f} percentage-point** gap to the approximate paper
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

{table(endpoint,list(endpoint.columns))}

Higher LTL/TL makes long customer legs less attractive, flattening the curves.
On the untouched original inputs even ratio 1 yields only about 0.815%, so the
ratio alone cannot explain the paper's nearly 18%. Synthetic product mix, source
availability, spatial demand and inventory/capacity assumptions differ. Both the
original and corrected controlled experiments are in the workbook. Figure 4 contains
only current synthetic inputs; archived synthetic comparisons are in appendix
Figure A1 and are not described as historical validation. All four archived source
files are included under backups/ with relative paths and SHA-256 in archive_provenance.

All eight configurations were re-solved at every ratio (56 LPs). Preferred choices:

{table(freight_best,['rate_ratio','config','overall_cost'])}

Fees were multiplied uniformly; flows need not be re-solved because a fixed fee
is constant within a configuration. Preferred choices:

{table(fixed_best,['fixed_cost_multiplier','config','overall_cost'])}

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
'''


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        print(pd.DataFrame(self_tests()).to_string(index=False))
    else:
        main(*(sys.argv[1:3]))


