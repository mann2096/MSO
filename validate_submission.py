"""Read-only acceptance checks for final artifacts; run after nukote_lp.py."""
from pathlib import Path
import hashlib
import json
from zipfile import ZipFile

import numpy as np
import pandas as pd
from PIL import Image
import nukote_lp as model


def main():
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    source = model.load('nukote_synthetic_data_v2.xlsx')
    results = model.load('nukote_results.xlsx')
    metadata = results['run_metadata'].set_index('parameter').value
    for key,path in [('input_sha256','nukote_synthetic_data_v2.xlsx'),
                     ('generator_sha256','generate_nukote_data_v2.py'),('lp_script_sha256','nukote_lp.py')]:
        check('source_hash_'+path,metadata[key]==hashlib.sha256(Path(path).read_bytes()).hexdigest())
    for name,frame in source.items():
        pd.testing.assert_frame_equal(frame,results[('input_'+name)[:31]],check_dtype=False,check_exact=True)
        check('input_copy_'+name,True)
    for path in ['nukote_synthetic_data_v2.xlsx','nukote_results.xlsx']:
        with ZipFile(path) as archive:
            check('xlsx_integrity_'+path,archive.testzip() is None)
    cfg = results['configurations']
    check('eight_feasible_configurations',len(cfg)==8 and (cfg.status=='FEASIBLE').all() and (cfg.solver_status==0).all())
    check('configuration_components',np.allclose(cfg[['transport','holding','handling','fixed_cost']].sum(axis=1),cfg.overall_cost,atol=.01,rtol=0))
    check('configuration_normalization',np.allclose(cfg.rel_overall,cfg.overall_cost/cfg.overall_cost.iloc[0],atol=1e-12,rtol=0))
    check('no_failed_model_checks',not (results['validation_checks'].result=='FAIL').any())
    check('no_failed_generator_checks',(source['checks'].result=='PASS').all())
    for name,groups in [('radius_sweep',[results['radius_sweep']]),
                        ('rate_ratio_sensitivity',[g for _,g in results['rate_ratio_sensitivity'].groupby('rate_ratio')])]:
        for i,group in enumerate(groups):
            check(f'{name}_{i}_radii',group.radius.tolist()==[1000,1500,2000,2500,3000])
            check(f'{name}_{i}_monotonic',bool((np.diff(group.lp_cost)<=.01).all()))
            check(f'{name}_{i}_normalization',np.allclose(group.normalized_cost,group.lp_cost/group.lp_cost.iloc[0],atol=1e-12,rtol=0))
    paper = results['paper_reference']
    check('five_point_paper_reference',paper.radius_miles.tolist()==[1000,1500,2000,2500,3000] and np.allclose(paper.normalized_reference,[1,.9,.82,.82,.82]))
    workbook_hash = hashlib.sha256(Path('nukote_results.xlsx').read_bytes()).hexdigest()
    images = ['fig2_configurations.png','fig3_radius.png','fig4_rate_ratio.png']
    if 'archived_rate_sensitivity' in results:
        images.append('figA1_archived_rate_ratio.png')
        for row in results['archive_provenance'].itertuples(index=False):
            path = Path(row.file)
            check('archive_source_'+path.name,path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==row.sha256)
    for filename in images:
        with Image.open(filename) as image:
            check('figure_run_'+filename,image.info.get('RunID')==metadata['run_id'])
            check('figure_workbook_'+filename,image.info.get('SourceWorkbookSHA256')==workbook_hash)
            check('figure_dimensions_'+filename,image.width>=1200 and image.height>=700)
            image.verify()
    # Independently recheck the complete exported best-flow table against raw inputs.
    best = cfg.loc[cfg.overall_cost.idxmin()]
    certificate = results['solver_certificates'].set_index('config').loc[best.config].to_dict()
    outbound = best.outbound.split(',')
    wc = source['distances'].query("lane_type=='W-C'")
    reachable = set(wc.loc[wc.from_id.isin(outbound)&(wc.miles<=best.radius_miles),'to_id'])
    customer_ids = set(source['customer_demand'].customer_id)
    record = dict(certificate,V=results['best_flows'],outbound=outbound,radius=float(best.radius_miles),
                  objective=float(best.lp_cost),fixed_cost=float(best.fixed_cost),total_cost=float(best.overall_cost),
                  exceptions=sorted(customer_ids-reachable),eligible_wc_arcs=int(best.eligible_wc_arcs))
    verified = model.validate_solution(source,record)
    check('exported_best_flow_independent_checks',all(c['result']=='PASS' for c in verified))
    manifests = sorted(Path('backups').glob('*/protected_files_sha256.json'))
    check('protected_manifest_present',bool(manifests))
    for name,digest in json.loads(manifests[-1].read_text(encoding='utf-8')).items():
        path = Path(name)
        check('protected_unchanged_'+name,path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==digest)
    for filename in ['generate_nukote_data_v2.py','nukote_lp.py','MODEL_VALIDATION.md','README.md']:
        text = Path(filename).read_text(encoding='utf-8')
        check('portable_paths_'+filename,all(s not in text for s in ['C:/Users/','C:\\Users\\','.cache/codex-runtimes','sys.path.append']))
        check('encoding_'+filename,'\ufffd' not in text and '?' not in text and '\u00c3' not in text)
    comparison = results['solver_comparison']
    check('comparison_honest',set(comparison.result)<=set(['PASS','SKIP']))
    if (comparison.result=='SKIP').any():
        print('SKIP: fresh HiGHS/NumPy comparison unavailable; see solver_comparison.')
    print(f'Acceptance: {len(checks)} checks PASS; 0 FAIL. Exported best-flow checks: {len(verified)} PASS.')
    return checks


if __name__=='__main__':
    main()
