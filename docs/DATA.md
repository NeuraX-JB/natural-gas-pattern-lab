# What each date means

The public demo covers U.S. Henry Hub **cash-market** observations. Its five-quote outcomes advance five rows in the observed spot-price series. An exchange calendar, Sunday-night reopen, individual delivery month and official futures settlement are separate pieces of information. Use the futures importer for questions that require them.

| Input | Frozen coverage |
| --- | --- |
| Henry Hub cash | 4,524 observed prices, 2008-01-02 to 2025-12-31 |
| Storage | 673 dated editions, 2012-01-05 to 2025-12-18; 832 distinct storage Fridays |
| Weather | 103 complete paired forecasts from a planned sample of 120 first Wednesdays |

## Henry Hub spot

The shipped series has 4,524 numeric observations from January 2, 2008 through December 31, 2025. This is the dense, complete-calendar-year portion of the source available at the November 24, 2026 build. All shipped dates and values were cross-checked against FRED series DHHNGSP. The maximum gap between consecutive observations is five calendar days. Some years contain weekday-holiday observations. A quote gap carries no implied flat return. The source's blank entry for January 5, 2018 was dropped.

The price is U.S. dollars per million British thermal units (USD/MMBtu) at Henry Hub, Louisiana. Dates identify cash-market observations. EIA publishes a weekly release that can contain several daily observations, and a subsequently downloaded history may include revisions. The quote-date replay uses this frozen edition. A sequence can be studied retrospectively. The file cannot establish which quotes someone reading EIA's website had at a particular hour or the price at which they could trade. A separate monthly decision replay uses historical ALFRED releases and records the latest quote actually present on each decision date. [Vintage study](VINTAGES.md)

The CSV comes from the public DataHub package at its pinned Git revision. The package declares ODC-PDDL-1.0 for its database. EIA identifies Refinitiv, an LSEG business, as the upstream spot-price source. FRED tags DHHNGSP “Public Domain: Citation Requested.” The code license applies to the project code. For other data products or live feeds, consult their own provider terms. [Pinned CSV](https://github.com/datasets/natural-gas/blob/fe9c40ee28516489b055736045746dd7faf00898/data/daily.csv) · [Data package license declaration](https://github.com/datasets/natural-gas/blob/fe9c40ee28516489b055736045746dd7faf00898/datapackage.json) · [PDDL](https://opendatacommons.org/licenses/pddl/1-0/) · [FRED series](https://fred.stlouisfed.org/series/DHHNGSP) · [EIA source definitions](https://www.eia.gov/dnav/ng/TblDefs/ng_pri_fut_tbldef2.asp)

## Lower 48 working gas

`storage.csv` preserves what specific editions of EIA’s *Natural Gas Weekly Update* reported. For each accepted edition we match the printed release date to EIA's archive entry, extract its dated working-gas history (Bcf), and keep the first available value and subsequent changes for each storage Friday. When available, the latest Friday also carries the five-year average shown in the edition’s national historical-comparisons table. The parser verifies that the inventory and average reproduce the published percentage within rounding tolerance. Older weeks first seen together in a later edition receive that later date.

`published_at` is a conservative **known-by time**, 23:59 America/New_York on the printed release date. This companion publication provides date-level evidence. The timestamp does not reconstruct the intraday Weekly Natural Gas Storage Report release. Holiday or missing archive editions stay missing until the next included edition supplies evidence. Revisions can repeat a `period_end` with a new `published_at`. The engine uses the most recent eligible vintage. Stock counts on a chart are EIA survey estimates, not delivery volumes.

For example, the July 18, 2019 edition reported 2,533 Bcf for Friday, July 12, against a five-year average of 2,676 Bcf. This edition supports a deficit of 143 Bcf at that day's cutoff. Each edition and the hash of its extracted numeric series are indexed in `data/storage_issues.csv`. Its URL follows the same pattern as the example. EIA permits reuse of its own government information products with acknowledgment. Source credit goes to the U.S. Energy Information Administration for the dated editions in the index. [Example archived issue](https://www.eia.gov/naturalgas/weekly/archivenew_ngwu/2019/07_18/) · [Issue index](https://www.eia.gov/naturalgas/weekly/includes/archive.php) · [Survey and historical comparisons](https://ir.eia.gov/ngs/methodology.html) · [EIA reuse terms](https://www.eia.gov/about/copyrights_reuse.php)

## Population-weighted degree-day forecast revisions

The weather extract uses NOAA Climate Prediction Center seven-day forecasts based on National Digital Forecast Database (NDFD) grids. It compares the prior Tuesday run with the **first Wednesday of each calendar month** from 2016 through 2025. The source checks accepted 103 of 120 planned pairs. The 17 exclusions and their reasons are in the manifest; 101 retained forecast pairs coincide with an observed spot quote. Weather is selectable for exact-event filters. The standard analogue comparison omits it; `--analogue-weather` adds it explicitly. This predetermined sampling rule represents a small set of calendar days. A pattern frequency measured on this extract describes the sampled days with eligible price observations. It must not be interpreted as the frequency in the entire date range.

For each run we read the dated `Population.Heating.txt` and `Population.Cooling.txt` files' CONUS rows. They contain daily Fahrenheit heating or cooling degree-days with a 65°F base, weighted by population. The issued-at timestamp comes from each file's header. The availability timestamp is the latest of the four NOAA archive files' HTTP Last-Modified headers, which must each fall on the matching initialization date. This provides file-version timing evidence, not a second-by-second independent record of public receipt. The earlier and later grids must have six shared valid dates. The engine totals revisions on exactly the five dates following the query date. The archive's rounded daily values can sum to a slightly different number from NOAA's independently rounded weekly total.

`data/weather_sources.csv` indexes run date, heating/cooling product, archive modification time (UTC) and SHA-256. Source URLs are reconstructed as `.../daily_forecasts_7day/YYYY/MM/DD/Population.Heating.txt` or `Population.Cooling.txt`. No observed degree-day values have been substituted for a missing forecast. NOAA's own materials are generally public domain unless otherwise marked. [CPC degree-day methodology and NDFD forecast link](https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/) · [Dated archive](https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_forecasts_7day/) · [Example forecast](https://ftp.cpc.ncep.noaa.gov/htdocs/degree_days/weighted/daily_forecasts_7day/2025/05/15/Population.Heating.txt) · [NOAA terms](https://www.noaa.gov/disclaimer)

## Reproduce the snapshot

`data/manifest.json` records coverage, row counts and SHA-256 hashes. Run `python data/rebuild.py --verify-only` to verify the five input and source-index hashes offline. The demo uses the committed CSVs. To audit the exact edition against source files, run these two commands from the repository root with internet access.

```sh
python data/rebuild.py --output restored --archive-cache cache/eia
python data/rebuild_weather.py --output restored --cache cache/noaa
```

These commands verify numerical data against the frozen manifest. A source correction or archive timestamp change requires review. Local cache directories allow resuming a build; data CSVs are written after all checks pass. The commercial futures history used by an individual researcher has its own vendor and data rights and is supplied locally.
