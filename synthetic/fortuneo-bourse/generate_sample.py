#!/usr/bin/env python3
"""Generate a synthetic Fortuneo "Bourse" (stock account) export.

The format is byte-compatible with the real export:
- ISO-8859-1 encoding, ';' delimited, 11 columns (last one empty)
- wrapped in a zip whose inner file name starts with
  "HistoriqueOperations" and ends with ".csv"
- header fields are taken verbatim from the installed
  beancount_fortuneo.StockAccountImporter.FIELDS so the importer's
  identify() header check is guaranteed to pass.

All companies, tickers, amounts and dates are synthetic.
Run with: uv run generate_sample.py
"""

import os
import zipfile

from beancount_fortuneo import StockAccountImporter

BASENAME = "HistoriqueOperationsBourse_000000000000_du_01_07_2025_au_30_06_2026"
HERE = os.path.dirname(os.path.abspath(__file__))

# (libellé, Opération, Place, Date, Qté, Prix d'éxé, Montant brut,
#  Courtage/Prélèvement, Montant net, Devise)
# Rows sorted by date descending, like the real export.
ROWS = [
    # Cash buy of the ETF tracker (120 * 5.60 = 672.00)
    ("GAMMA WORLD UCITS ETF - Acc EUR ACC", "Achat Comptant", "Euronext Paris", "15/06/2026", "120.0", "5.6", "-672.0", "-2.69", "-674.69", "EUR"),
    # Dividend received (the only real cash event of the 26/04 cluster below)
    ("ACME CORP", "Encaissement coupons intérêt/dividende", "Euronext Paris", "28/04/2026", "50.0", "0.0", "25.0", "0.0", "25.0", "EUR"),
    # Bookkeeping-noise cluster: the coupon "creation" appears in EUR and
    # USD, and the USD one is then cancelled. See README.md.
    ("ACME CORP", "OST de création de coupons - Détachement coupon optionnel", "Euronext Paris", "26/04/2026", "50.0", "0.0", "25.0", "0.0", "25.0", "EUR"),
    ("ACME CORP", "OST de création de coupons - Détachement coupon optionnel", "Euronext Paris", "26/04/2026", "50.0", "0.0", "28.4", "0.0", "28.4", "USD"),
    ("ACME CORP", "ANNUL. OST de création de coupons - Détachement coupon optionnel", "Euronext Paris", "26/04/2026", "50.0", "0.0", "-28.4", "0.0", "-28.4", "USD"),
    # Cash sale (50 * 14.00 = 700.00)
    ("ACME CORP", "Vente comptant", "Euronext Paris", "05/03/2026", "50.0", "14.0", "700.0", "-2.0", "698.0", "EUR"),
    # Zero-value bonus subscription (250 free shares)
    ("DELTA SA", "OST de création avec ou sans droits - Souscription avec droit", "Euronext Paris", "12/01/2026", "250.0", "0.0", "0.0", "0.0", "0.0", "EUR"),
    # Cash buy of the stock (100 * 12.34 = 1234.00)
    ("ACME CORP", "Achat Comptant", "Euronext Paris", "30/11/2025", "100.0", "12.34", "-1234.0", "-2.0", "-1236.0", "EUR"),
    # Zero-value automatic attribution (3 bonus shares)
    ("BETA INDUSTRIES", "OST de création avec ou sans droits - Attribution automatique", "Euronext Paris", "18/09/2025", "3.0", "0.0", "0.0", "0.0", "0.0", "EUR"),
    # Cash buy with one-decimal amounts, as the real export prints them
    # (10 * 88.90 = 889.00; 889.00 + 1.50 = 890.50 printed as -890.5)
    ("BETA INDUSTRIES", "Achat Comptant", "Euronext Paris", "04/08/2025", "10.0", "88.9", "-889.0", "-1.5", "-890.5", "EUR"),
    # Cash buy of the ETF tracker (80 * 5.55 = 444.00)
    ("GAMMA WORLD UCITS ETF - Acc EUR ACC", "Achat Comptant", "Euronext Paris", "02/07/2025", "80.0", "5.55", "-444.0", "-1.78", "-445.78", "EUR"),
]


def main():
    # Header fields straight from the importer, so identify() always passes.
    fields = list(StockAccountImporter.FIELDS)
    assert fields[-1] == "", "expected an empty trailing column"
    assert len(fields) == 11

    lines = [";".join(fields)]
    for row in ROWS:
        assert len(row) == 10
        lines.append(";".join((*row, "")))

    csv_path = os.path.join(HERE, BASENAME + ".csv")
    with open(csv_path, "w", encoding="iso-8859-1", newline="\n") as f:
        f.write("\n".join(lines) + "\n")

    zip_path = os.path.join(HERE, BASENAME + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(csv_path, BASENAME + ".csv")

    print(f"wrote {csv_path}")
    print(f"wrote {zip_path}")


if __name__ == "__main__":
    main()
