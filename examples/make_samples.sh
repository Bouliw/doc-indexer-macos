#!/bin/bash
# Creates a folder of fictitious documents to try extract_texts.py on.
# Only uses tools that ship with macOS: cupsfilter, qlmanage, sips, textutil, say.
# Usage: examples/make_samples.sh [OUT_DIR]   (default: examples/sample-docs)
set -euo pipefail

OUT="${1:-$(dirname "$0")/sample-docs}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT/bills" "$OUT/housing" "$OUT/health" "$OUT/notes"

# Text file -> PDF with a real text layer
text_pdf() { cupsfilter -o lpi=4 -m application/pdf "$1" > "$2" 2>/dev/null; }

# Text file -> JPEG, like a phone photo of a paper document (no text layer at all)
photo() {
  cupsfilter -o lpi=4 -o cpi=8 -m application/pdf "$1" > "$TMP/page.pdf" 2>/dev/null
  qlmanage -t -s 2200 -o "$TMP" "$TMP/page.pdf" >/dev/null 2>&1
  sips -s format jpeg "$TMP/page.pdf.png" --out "$2" >/dev/null
}

cat > "$TMP/invoice.txt" <<'EOF'
ACME ENERGY
Electricity invoice - September 2026

Customer: Jane Doe
Contract: residential, fixed price
Consumption: 212 kWh

Amount due: 48.90 EUR
Due date: 15 October 2026
EOF
text_pdf "$TMP/invoice.txt" "$OUT/bills/acme-energy-invoice.pdf"

cat > "$TMP/rent.txt" <<'EOF'
RENT RECEIPT - August 2026

Received from Jane Doe the sum of 750.00 EUR
for the rent of Flat 3B, 12 Sample Road, Springfield.

Rent: 690.00 EUR
Service charges: 60.00 EUR

Signed: John Smith, landlord
EOF
photo "$TMP/rent.txt" "$TMP/rent.jpg"
cupsfilter -m application/pdf "$TMP/rent.jpg" > "$OUT/housing/rent-receipt-scan.pdf" 2>/dev/null

cat > "$TMP/pharmacy.txt" <<'EOF'
CITY PHARMACY
Receipt 0412 - 03/09/2026

Ibuprofen 400 mg        3.20 EUR
Saline nasal spray      4.10 EUR

TOTAL                   7.30 EUR
Paid by card
EOF
photo "$TMP/pharmacy.txt" "$OUT/health/pharmacy-receipt.jpg"

cat > "$TMP/lease.txt" <<'EOF'
RESIDENTIAL LEASE AGREEMENT

Landlord: John Smith
Tenant: Jane Doe
Property: Flat 3B, 12 Sample Road, Springfield
Term: 12 months from 1 September 2026, renewable
Monthly rent: 690.00 EUR plus 60.00 EUR of service charges
Deposit: 690.00 EUR, returned within one month after the tenant leaves
Home insurance: the tenant must provide a certificate every year.
EOF
textutil -convert docx "$TMP/lease.txt" -output "$OUT/housing/lease-agreement.docx"

cat > "$OUT/notes/todo.md" <<'EOF'
# To do
- Send the home insurance certificate to the landlord
- Check the electricity meter reading in October
EOF

say -o "$OUT/notes/voice-memo.m4a" --data-format=aac "Remember to renew the home insurance before November."

echo "Sample documents written to $OUT"
