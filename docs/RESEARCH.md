# Reading a pattern study

The spot study asks what happened across the next five **published Henry Hub spot observations**. The outcome is the ending price minus the starting price in dollars per MMBtu. Weekends, holidays and missing observations can extend the calendar interval. A flat outcome counts as nonpositive. Neither result is a tradable futures return.

## Rules discovered from history

The screen has ten predeclared templates, listed in the report settings. Examples are previous direction + current direction, five-session trend + volatility regime, and current direction + published storage category. Each distinct tuple of categorical values creates one exact rule. Missing values cannot produce a rule. Equality describes the named categories only.

By default, the first 70% of observations form the training period. Choose a fixed first holdout session using `--split-date YYYY-MM-DD` to keep the split stable while adding new data. Five-observation training outcomes must finish before that session. Holdout starts at the split and stops soon enough to observe every outcome by the cutoff. Within each rule and period, the screen retains the earliest occurrence and skips further matches through its endpoint. Individual rules can describe the same episode.

The screen counts how often price rose for each rule and compares it with the frequency for **all eligible session starts in the same seasons and period**. For example, if eight rule matches came in winter and two in spring, the season comparison is 0.8 × winter's positive rate + 0.2 × spring's positive rate. This is a descriptive reference and can include the rule's own dates. Holdout reference frequencies use realized holdout outcomes for retrospective comparison.

Rules need 12 distinct training episodes by default. Their training score equals the absolute difference between the rule and seasonal positive rates, multiplied by n/(n+30) to reduce the influence of rare rules. The training results determine the rank and whether the rule went up unusually often or unusually rarely. The JSON field `tested_rules` counts distinct candidate tuples enumerated in training, including tuples below minimum support. The field `eligible_rules` counts the tuples meeting the training minimum. The top eight advance unchanged. Every selected rule is reported regardless of holdout performance, including rules with fewer than five holdout episodes, identified as thin. Each row includes the actual event dates and price changes and the earliest outcome that went against the training direction when present. This opposite-outcome example illustrates variability. A single observation cannot establish or overturn a relative-frequency claim.

Several rules are tested. Related rules can select the same events. The holdout helps reveal failed patterns but does not establish causality or statistical significance. Adjusting definitions after viewing the holdout makes those dates part of development. Reserve fresh dates for a further test.

## Walk-forward forecast comparison

At each evaluation origin the matcher receives only the input prefix through that observation. Its forecast is the median five-observation dollar change among up to ten distinct eligible analogues. The season-only forecast is the median historical five-observation change from starts in the query season whose full ten-observation windows ended **before** the query, matching the maturity requirement of the displayed analogues. Both forecasts are scored on exactly the same origins. Origins with fewer than three matches or without a seasonal forecast are skipped and counted.

Defaults reserve 252 observations for warmup and schedule every fifth eligible origin. All scheduled origins are scored by default. An end date limits both inputs and realized outcomes. Specify `--max-queries 100` to evaluate the most recent 100 scheduled origins, or `--stride 1` for a daily replay. The report records all settings, coverage and every paired forecast. Results can change with the evaluation interval and the match definition.

MAE is mean absolute forecast error in $/MMBtu. **MAE difference = analogue MAE − seasonal MAE.** Negative favors the analogue. The approximate 95% interval is a percentile interval from 1,000 seeded circular moving-block bootstrap resamples of the chronological paired error differences. The default block covers ten scored origins, and the block is at least as long as a five-observation forward window in scheduled-origin units. An interval is omitted if fewer than 20 pairs or two full blocks are available. This interval assumes the evaluation period is reasonably representative and can be sensitive to block length, gaps and changes in market behavior.

## Commands

From the repository root, the default input is `data/spot.csv`. Add `--storage FILE` and `--weather FILE` for validated auxiliary series when appropriate.

```sh
python -m gaslab.research discover --out rules.json
python -m gaslab.research evaluate --out walk_forward.json
```

The JSON files include all dates, exclusions and units where applicable. The data documentation describes publication timing, revisions and what the spot replay can support.

## What an exact match compares

Direction is the sign of the difference between consecutive observed prices. A five-observation trend compares today's price with the price five observations earlier. A signed-cent condition rounds the decimal dollar change to the nearest cent, with half-cent ties rounded away from zero. A ten-token sequence fixes the order of ten consecutive differences. A wildcard accepts one difference at its position. It never skips a variable number of observations.

The volatility feature is the population standard deviation of the latest five changes in dollars. The low and high thresholds come from the previous 252 available rolling values, with at least 60 required. Values below the historical lower quartile are low, those above the upper quartile are high and the remainder are normal. Seasons are meteorological quarters. Storage surplus and deficit refer to working gas versus the five-year comparison known at the observation cutoff. Weather records the signs of the total HDD and CDD revisions over the same next five calendar dates. A missing value does not satisfy a checked condition.

For exact matches, the explorer selects nonoverlapping episodes in chronological order. For analogues it first sorts by similarity, then skips any window that would overlap an already selected episode. The ordinary window runs from five observations before the match through ten afterwards. These choices prevent one cluster from dominating the chart. They do not establish statistical independence.

## How similar is an analogue?

Each active feature has equal weight by default. Continuous differences use absolute distance divided by the historical population standard deviation of that feature, estimated from eligible earlier anchors. A season mismatch costs one standardized unit. The overall distance is the square root of the weighted mean of squared component distances. A candidate must have every active input and a distance at most 1.0. By default the interactive lab and its bundled paired evaluation use one- and five-observation changes, five-change volatility, season, and the storage gap when known on the anchor date. The weather sample is sparse. The optional top-level flag `--analogue-weather` includes its HDD and CDD magnitudes on dates where both can be computed. Fixed weights and the 1.0 threshold are design choices, not optimized forecasts.

The season-only exact-rule display first restricts baseline dates to those with observed values for every checked field, sufficient sequence history and a matured outcome. It then aligns seasonal weights with the proportions of distinct matched episodes. This keeps ordinary days with no sampled weather forecast out of a weather-rule comparison. The curve plots weighted median prices rebased to 100. The dollar statistic is the weighted median dollar change. Each quantity is calculated in its own units; the percentage implied by one need not equal the other. Query-date lookback is a reference line and can differ from a custom rule.
