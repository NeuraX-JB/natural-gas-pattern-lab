# A replay using the spot prices available at the time

The dashboard’s daily spot history and this monthly decision study answer different questions. Here each decision is backed by an actual dated ALFRED archive. For example, on Friday, February 2, 2024, the latest published daily Henry Hub observation in the archive was Tuesday, January 30. The February 2 spot quote was already in the past by that evening, but the archive had yet to publish it.

From the repository root, run:

```sh
python -m gaslab.vintage
python -m gaslab.vintage --date 2024-02-02
```

The first command evaluates the frozen offline study. The second shows everything a researcher could use for a sampled decision. It includes the ten nearest earlier decisions, their feature differences, their outcomes, and all prior occurrences with identical signs on three moves. The selected decision’s own later outcome is omitted from its replay. See the dated [scored forecasts](../reports/vintage_scores.csv) to grade the forecast.

## What the published archives showed

There were 82 eligible decisions from January 4, 2019 through November 7, 2025. The ten-neighbour forecast missed by **$0.489/MMBtu** on average, versus **$0.482/MMBtu** for season alone. The difference was +$0.007/MMBtu, with a paired block-bootstrap 95% interval from −$0.014 to +$0.029. These sampled decisions did not establish an improvement from numerical matching. The age of the latest published price across all 132 archived dates ranged from one to ten calendar days, with a median of three days.

## A pattern that repeated, with different outcomes

Run `python -m gaslab.vintage --date 2020-01-03`. In the January 3, 2020 archive, the two latest one-observation price moves were both down and the latest known spot price was below its level five observations earlier. Six earlier first-Friday decisions matched all three conditions exactly. The fifth quote after the decision finished above the latest then-published mark in three of those six. On August 3, 2018 the subsequent change was +$0.21/MMBtu; for June 7, 2019 it was −$0.13/MMBtu. Both dates belong in the evidence table.

## A fixed schedule and a dated outcome

- Download one 80-calendar-day window of observations as known at the end of the first Friday of each month, January 2015 to December 2025. Keep the calendar origin even if Friday was a holiday. The source provides date-level vintage evidence, so the study makes no intraday availability claim. ALFRED’s DHHNGSP release history begins December 10, 2014.
- At each origin, retain only rows published in that vintage. Omit blank quotes and use the latest six reported observations to compute changes and volatility. If any of the six has a nonpositive mark, flag and exclude the entire decision from this percentage-based model. The January 1, 2021 archive reported 0.00 for December 23 and 24, 2020. Those dates are preserved in the source record. Replacing a historical input with a later vintage would change the question.
- Define the outcome as the **fifth spot observation whose date is strictly after the decision date, minus the latest known spot price in the decision archive**, in dollars per million Btu. Take that future observation from the *next* scheduled vintage and store its quote date and the vintage that verifies it. If the source adds an old observation after a decision, its old date cannot count as a future day. The archived latest known price can be several days old, so this target measures change from a published reference mark. It does not represent a filled trade. Missing quotes and holidays lengthen the elapsed calendar interval.
- At forecast time include an earlier event only when its outcome has already appeared in a sampled archive by the decision. Keep the last decision available for matching, with its outcome ungraded because no 2026 vintage is bundled.

## Matching and grading

Exact matches require the previous one-observation move, latest one-observation move and five-observation net move to have the same direction as the selected decision. Up, Down and Flat are separate categories. These are identical event labels. Each dated outcome stays visible, including adverse outcomes.

For numerical analogues compute the percent change over one and five observations and the population standard deviation of the five simple percentage changes. For each continuous variable, divide the absolute difference by its median absolute deviation among all eligible earlier decisions. When that denominator is zero, use its historical population standard deviation, then one if constant. Give season a distance of zero for agreement or one otherwise. Average these four contributions equally. Earliest decision breaks ties. Take the median dollar outcome among the ten closest dates. The benchmark uses the median among all earlier eligible decisions in the same decision-date meteorological season (Dec–Feb, Mar–May, Jun–Aug, Sep–Nov). This is a separately specified monthly model. Its sampled decision set, features and outcome differ from those in the daily lab.

Scoring begins once the first 48 monthly decisions have matured and at least three same-season decisions are available. Both models are graded on the same decision dates. A positive mean absolute error difference means the analogue forecast missed by more; a negative value means it missed by less. A paired circular moving-block bootstrap resamples consecutive three-month runs of differences, preserving short-run dependence. The fixed seed is 7351 and the interval uses the 2.5th and 97.5th percentiles of 2,000 replicates. Its interpretation depends on the sampled months and the chosen block length. It cannot establish causation or generalize to futures fills or every trading day.

## Sources and reproducibility

The source is the U.S. Energy Information Administration, Henry Hub Natural Gas Spot Price (DHHNGSP), as archived by ALFRED at the Federal Reserve Bank of St. Louis. The FRED series metadata tags the series **Public Domain: Citation Requested**. This study distributes derived features and changes, dates, source URLs, and hashes. Each URL in the manifest retrieves the corresponding original dated CSV from ALFRED.

- [ALFRED series and release-history start](https://alfred.stlouisfed.org/series?seid=DHHNGSP)
- [ALFRED’s definition of a vintage date and the data download form](https://alfred.stlouisfed.org/series/downloaddata?seid=DHHNGSP)
- [FRED source, units, series tags and attribution](https://fred.stlouisfed.org/series/DHHNGSP)
- [Frozen decision rows](../data/vintages/monthly.csv) · [Source URLs and raw SHA-256 hashes](../data/vintages/manifest.csv) · [Score summary](../reports/vintage_summary.json)

The offline files contain no raw vendor price history. For independent reproduction with network access, regenerate the source-derived files and scores with the command below. It validates each requested vintage identifier, rejects future-dated observations, and writes a SHA-256 digest for each raw response. Compare the regenerated CSVs and manifest with the published Git version. The optional cache should be outside the repository.

```sh
python -m gaslab.vintage --refresh --cache ../alfred-vintage-cache --write-reports
```
