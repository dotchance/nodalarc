# FCC Starlink Gateway Research Findings
**Date:** 2026-04-25
**Source:** FCC IBFS filings, satsim/starlink_gateways.json dataset

## Summary
- 104 total entries from FCC IBFS filings
- 71 with filing-exact DMS coordinates (converted to decimal)
- 33 with city-level geocoded coordinates (filing exists, DMS not extracted)
- All have FCC filing numbers linking to real IBFS records

## Three Antenna Generations
1. Gen 1 (2019-2020): 8x 1.52m Ka-band
2. Gen 2 (2021-2023): 1x 1.47m or 1.85m Ka-band
3. Gen 3 (2024-2025): 32-40x 1.85m Ka+E band (71-86 GHz)

## Filing Number Format
- SES-LIC-YYYYMMDD-NNNNN — original license
- SES-MOD-YYYYMMDD-NNNNN — modification
- SES-STA-YYYYMMDD-NNNNN — special temporary authority
- Call signs: E19XXXX, E20XXXX, E21XXXX, E22XXXX, E24XXXX, E25XXXX

## Notes
- International gateways NOT in FCC IBFS — filed with national regulators
- alt_m values are USGS approximate elevations, not from filings
- Some locations have multiple filings (different antenna installations)
- Guam entries exist since Guam is under FCC jurisdiction
- PDF attachments contain Schedule S with exact DMS — not parseable by web tools

## Verification URL Pattern
`https://fcc.report/IBFS/{filing_number}`

## Stations Needing Upgrade to Filing-Exact
The 33 geocoded entries have real filing numbers but coordinates need
to be extracted from the PDF application attachments. Priority: sites
with 32-40 antenna arrays (Gen 3 mega-gateways).
