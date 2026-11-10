# Nu-kote synthetic transportation LP

This is a synthetic academic case inspired by LeBlanc et al. (2004), not the
paper's confidential data. See MODEL_VALIDATION.md for assumptions and results.

## Run in a standard environment

Python 3.11 or newer is recommended. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install scipy
python generate_nukote_data_v2.py
python nukote_lp.py nukote_synthetic_data_v2.xlsx .
python nukote_lp.py --self-test
python validate_submission.py
```

SciPy/HiGHS is preferred automatically. If unavailable, omit its installation:
the existing NumPy fallback runs, and the fresh cross-solver comparison is marked
SKIP. Core dependencies use normal imports; no local runtime search paths are used.
The scripts preserve v1 inputs. Regeneration overwrites the named v2 outputs.

## Commands executed in this workspace

A project-local environment was prepared with system NumPy/pandas/matplotlib
and the already available pure-Python openpyxl and et_xmlfile packages placed in
its site-packages. Package-index access did not provide SciPy. The final workflow
uses these standard environment commands:

```powershell
$env:OPENBLAS_NUM_THREADS='1'
.\.venv\Scripts\python.exe generate_nukote_data_v2.py
.\.venv\Scripts\python.exe nukote_lp.py nukote_synthetic_data_v2.xlsx .
.\.venv\Scripts\python.exe nukote_lp.py --self-test
.\.venv\Scripts\python.exe validate_submission.py
```

## Deliverables and provenance

- The dataset workbook includes raw inputs, assumptions and seed-42 checks.
- The results workbook starts with a guide and includes full cost breakdowns,
  solver status/certificates, reproducibility checks, sensitivities and diagnostics.
- Figures 2-4 use the final workbook. Each PNG embeds its run ID and workbook hash.
- Figure A1 is a separate appendix for archived synthetic experiments. Its four
  source files are included under backups/20261017_132552 and recorded with hashes
  in the results workbook's archive_provenance sheet. They are not historical data.
- Timestamped backups preserve every pre-cleanup deliverable. Protected v1/PDF
  hashes are recorded before editing and checked by validate_submission.py.

The paper reference in Figure 3 is an approximate five-point visual interpretation
of the supplied PDF, not exact original observations. No historical freight
regression, 5% historical validation, or Excel Solver replication is claimed.
