English · [繁體中文](README.md)

# health-bank-toolkit

Converts the HTML exported from "My Health Bank" (健康存摺) in Taiwan's NHI Express app (健保快易通) into de-identified, structured JSON, **offline on your own machine**. The JSON can then be given to an AI to produce an interactive dashboard or a PDF health report.

- Uses only the Python standard library; no network access and no packages to install
- Name, national ID number, date of birth, phone number, address, medical record number, and NHI card visit serial number are always masked; the personal-information table is dropped entirely
- Medical institution, diagnosis, drug name, and lab values are useful data and are kept
- Free text such as imaging reports can only be de-identified by format and labels; a name without a label is not recognised (rules in `docs/health-bank-export-format.md` §7)
- On POSIX, a newly created output folder and the output files are readable and writable by the owner only; on Windows they inherit the output folder's permissions, so put the folder somewhere only you can read

> ⚠️ **Real data never goes into git.** Keep the original export and the parsed results in a private folder. If the output folder is inside any git working tree, the tool refuses to run.
>
> This tool and its templates make no diagnosis and do not suggest changing medication; reports and dashboards are both required to carry a medical disclaimer.

## Quick start

```bash
python tools/health_bank_parser.py <export folder | single HTML | ZIP> <output folder>
```

Output: one JSON file per category that has data, plus `summary.json` (record counts per category, date range, and which de-identification rules were applied).

| File | Content |
| :--- | :--- |
| `labs.json` | Lab and examination results (with high / low / normal / undetermined flags) |
| `outpatient.json`, `inpatient.json`, `surgery.json` | Outpatient visits, inpatient stays, and surgery (with order details) |
| `medications.json` | Medication |
| `dental.json`, `vaccinations.json` | Dental outpatient visits, vaccinations |
| `imaging_reports.json` | Imaging or pathology reports (free text, de-identified) |
| `directives.json` | Organ donation or hospice and palliative care directives |
| `generic.json` | Other tables |

The category is decided from the table header, not the file name. For a category with no data in this run, the file of the same name left by a previous run is deleted, so it cannot be mistaken for a current result.

## Contents

| Path | Purpose |
| :--- | :--- |
| `tools/health_bank_parser.py` | Offline de-identifying parser |
| `docs/health-bank-export-format.md` | Export format, table headers per category, date and lab interpretation rules, known pitfalls (written for an AI to read) |
| `templates/health-report-pdf-prompt.md` | Prompt for producing a PDF health report |
| `templates/health-bank-dashboard-prompt.md` | Prompt and JSON input contract for producing an interactive dashboard |
| `assets/health-bank-dashboard/index.html` | Single-file interactive dashboard example (entirely fictional data; opens directly in a browser) |
| `tests/` | Unit tests; `tests/fixtures/fake_health_bank.html` is an entirely fictional sample |
| `notes/` | Source analysis notes for this tool (originally in [ai-inbox](https://github.com/SanHsien/ai-inbox)) |

The documents, templates, and notes are written in Traditional Chinese.

## Tests

```bash
python -m unittest discover -s tests
```

CI runs on Ubuntu and Windows (without `PYTHONUTF8`, to make sure a Windows terminal does not fail on encoding either).

## Origin

Split out of [`SanHsien/ai-inbox`](https://github.com/SanHsien/ai-inbox) on 2026-10-25, keeping the git history of the related files.

## License

[MIT](LICENSE)
