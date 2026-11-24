# Weekend reopening study

**Question.** After the same Henry Hub futures delivery month reopens above its Friday settlement, how often is its Monday settlement higher than its reopening price? The study compares every qualifying gap with the full set of eligible weekends in the same meteorological season. The historical study requires a local futures file with actual weekend reopening trades and official settlement prices. The public spot series has neither type of futures observation.

## Run

From the repository root, with Python 3.10 or later:

```bash
python -m gaslab.weekend examples/weekends_synthetic.csv
python -m gaslab.weekend my_weekends.csv --json outputs/weekends.json --csv outputs/weekends.csv
python -m gaslab.weekend my_weekends.csv --season winter --min-gap 0.050
```

The bundled CSV contains **invented prices and illustrative schedule flags**. It demonstrates the format, a failed continuation and a contract switch. None of its rows constitutes historical market evidence. The program reads a local file and never uploads quotes. When using licensed data, keep it and any restricted exports outside a public repository.

## Prepare the CSV

Use one row per calendar Friday in a continuous date range, including holiday and incomplete weekends. Pick an explicit contract-selection rule before viewing weekend outcomes. One example is the nearest eligible listed delivery month whose **published last trading day is after Monday**, using the expiry calendar available by Friday. Apply the choice consistently. If a vendor rolls its front-month symbol over the weekend, retrieve all three observations for the one named delivery contract. If that cannot be done, preserve the differing identifiers and the importer will flag the event.

Use these exact column names. Additional columns can retain vendor record IDs or audit information.

| Field | Expected content |
|---|---|
| `friday_date` | Calendar Friday, YYYY-MM-DD, even when the exchange is closed. |
| `schedule` | `regular` only after verifying the product-specific Friday, reopening and Monday calendar. Use `holiday` for any holiday, early close, altered session or missing scheduled settlement. Use `unknown` when coverage is unverified. |
| `friday_contract`, `reopen_contract`, `monday_contract` | Actual NYMEX Henry Hub NG delivery month at each leg, e.g. `NG-2025-02`. Resolve vendor symbols to this format; continuous and adjusted series are unsuitable. |
| `friday_settle` | Friday's official settlement for that delivery month, in USD/MMBtu. |
| `reopen_date`, `reopen_price` | Calendar date in America/Chicago and price of the first actual trade after the scheduled weekend reopening. On a regular weekend this is Sunday evening. A vendor that stamps bars by trading day may label the same session Monday. Verify the underlying trade or bar semantics and local timestamp before recording its Sunday date. |
| `monday_date`, `monday_settle` | Monday trading date and official settlement for the same contract, in USD/MMBtu. |

Retain the original vendor, instrument, quote type, trade or publication timestamp and timezone, calendar version, selection rule and extraction time alongside your local source. Daily closing or spot prices do not supply a futures settlement or first post-weekend trade. Leave observations blank when they cannot be verified. Prices must be finite and quoted to at most three decimals. [CME contract specifications](https://www.cmegroup.com/markets/energy/natural-gas/natural-gas.contractSpecs.html) · [CME holiday and trading schedules](https://www.cmegroup.com/trading-hours.html) · [CME contract overview and tick size](https://www.cmegroup.com/education/courses/introduction-to-natural-gas/nat-gas-product-overview)

## Read the results

- A gap is reopening price minus prior Friday settlement. A gap qualifies if positive and at least the requested dollar threshold. The default admits every positive gap. A continuation is strictly positive Monday settlement minus reopening price. A tie is a failed continuation.
- For each season the report gives the eligible count, qualifying count, positive and flat counts, positive-move fraction, median change in USD/MMBtu and a 95% Wilson interval for a proportion. The interval treats observations as independent and ignores regime dependence. It describes sampling uncertainty under that assumption. Small samples can leave the direction very uncertain.
- A season uses the Monday trading date. Winter runs December–February, spring March–May, summer June–August and autumn September–November. In the combined benchmark, all eligible weekends within each represented season receive equal weight within that season. Season weights equal the fractions of qualifying gaps occurring in each season. The result estimates the comparison rate and median at the same season mix as the gap sample. Benchmark `n` counts distinct eligible weekends in those represented seasons. Per-season `all` fields expose the raw group counts.
- The gap group is a subset of the all-weekend comparison. These are observed conditional frequencies; identifying a cause or an independent trading edge would require additional evidence. `counterexample` returns the earliest dated qualifying gap with a flat or lower Monday settlement when one exists.
- The CSV audit records every calendar Friday in the supplied range and selected season, with either the event prices and outcome or an exclusion reason. A missing Friday between two submitted rows is marked `missing_weekend_row`. Coverage before the first or after the last supplied Friday is unknown. Exclusions report the first failing check for that weekend. Duplicate rows exclude the entire weekend. An invalid Friday anchor or missing required header halts the import so the analyst can repair the source.
- No market holiday calendar is guessed from civil dates. The importer trusts the supplied schedule verification, checks Sunday/Monday alignment and delivery IDs, and reports any supplied holiday/unknown rows separately. The data source must verify actual expiration and the official prices for all three legs. More missing openings in particular seasons or market conditions can bias the comparison.
