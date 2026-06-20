# Pinpoint Trading — Strategy-Fidelity Audit

**Source book:** *Pinpoint Trading* by Shake Pryzby (2025), 248 pp.
**Book file located at:** `~/Downloads/optimized_pinpoint-trading-compressed.pdf` (the cited `docs/pinpoint-trading.pdf` did not exist; no `docs/` dir existed before this report).
**Method:** 1:1 read of the book against the code, parallelized across focused readers, synthesized + reconciled here. Read-only — **no code was changed.**
**Page citations:** all are **PDF-PAGE** numbers (the marker pages in the extracted text). Printed page ≈ PDF-PAGE − 6 (verified: PDF-150 = printed 143; PDF-211 = printed 204; PDF-231 = printed 225).
**Audit run:** 2026-06-18 (filename keeps the requested `2026-06-17`).

---

## How to read this

Five buckets, per the brief: ✅ faithful · ⚠️ minor deviation (documented/justified) · 🔴 undocumented deviation (review/fix) · ❌ missing · ❓ ambiguous in book. Each item cites the book page, the code location, and a one-line verdict. Per the honest-constraints rule: where the book doesn't specify a number, it says **"not specified — our default."** Nothing here is fixed; fixes are a separate session.

**Headline:** the engine is highly faithful on the **screening / entry-trigger / stop / R:R / earnings-reaction / fundamentals** core. The real drift clusters in four places: (1) the **0-100 scoring weights, tiers, 5-state regime, and position-sizing exposure ladder are engineering constructs with no numeric basis in the book** (the book "grades" qualitatively and sizes by a 0.5%-risk formula); (2) the **measured-move price target** used to pre-qualify R:R is invented — the book prescribes *no* price target; (3) two **book-named core patterns (cup & handle, inverse H&S)** and the **entire exit-management discipline (EMA trail, 20%-over-5EMA climax trim)** are not implemented; (4) several **threshold constants** (bull-trap 50%-of-gap, quarter-stop −0.06, IPO 5-bar initial high, 7-day earnings floor) are reasonable house heuristics absent from the text.

---

## ✅ FAITHFULLY IMPLEMENTED

| Rule | Book (PDF-PAGE) | Code | Note |
|---|---|---|---|
| Price > $10 floor | "under $10 is my cutoff"; "Over $10" (133, 157, 171) | `config.UniverseGates.min_price=10` | Exact. |
| Avg volume ≥ 300k | "minimum of 300k average volume" (157, 171) | `min_avg_volume=300_000` | Exact. |
| Relative Volume > 2.0 | "Relative Volume > 2.0" (121) | `min_rel_volume=2.0` | Exact number. (`--ignore-rvol` is an off-hours operational add-on, not a book rule.) |
| RS rating threshold = 90 | "RS Rating > 90" (121) | `min_rs_rating=90` | Threshold value faithful (metric is a labeled proxy — see ⚠️). |
| MA period naming: 5/10/20 EMA, 50/200 SMA | "5, 10 & 20 EMA's are most useful"; 50 SMA intermediate; 200 SMA long-term (45-50, 57) | `RegimeConfig` ema_momentum/fast/signal=5/10/20, sma_intermediate/macro=50/200 | Periods + EMA-vs-SMA roles match. |
| Beta / ADR are *preferences*, not gates | book discusses range qualitatively; no beta/ADR gate | `preferred_beta`, `preferred_adr_pct` — explicitly not gated | Correctly preference-only; no fabricated gates in `evaluate_gates`. |
| Earnings-flag detector (⭐) | gap-up pole on volume → light-volume flag off 5/10/20 EMA (10 best) → volume-expansion breakout (88-90) | `patterns.detect_earnings_flag` | Strongest match in the file; 10-EMA preferred on ties, exactly per book. |
| High & tight flag | "90-100% or more within 1-8 weeks" then "tight… 1-3 weeks" (99-101) | `detect_high_tight_flag` (surge 90%/40 bars, flag 15 bars) | 90% surge + 8-wk pole + 3-wk flag all match. |
| Descending channel (bullish, in uptrend) | parallel lower-highs/lows in a greater uptrend, light volume, break up (95-98) | `detect_descending_channel` | Faithful incl. parallelism + uptrend gate. |
| Inside day (entry tactic) | "lower high and a higher low"; entry through high, stop below low / prior low (≈188) | `detect_inside_day` | Definition + stop rule exact. |
| EMA reclaim (20-EMA recapture buy) | "First recapture of the 20 EMA gives us our first buy signal" (65-68) | `detect_ema_reclaim` | Faithful single-signal version. |
| Flat-base concept | flat base = a base-breakout form; ~6-week ideal; volume-dryup then surge (82-83) | `detect_flat_base` | Concept/trigger/volume faithful (depth/near-high caps are house — see ❓). |
| Growth thresholds 25/25/25, triple-digit 100% | "25%+ EPS YoY"; "25%+ annual… 3-5 years"; "25% annualized sales"; "triple digit (100%+)" (160, ≈133) | `FundamentalThresholds` 25/25/25/100 | All four numbers exact. |
| Growth = scoring layer, NOT a hard gate (chart-first) | "The chart tells the story"; reaction leads, "then I dive into the report" (133, 157-160) | growth is a layer; hard filters only with `--min-growth` | Chart-first hierarchy honored. |
| Acceleration as top fundamental signal | "Accelerating Earnings Growth… the top fundamental indicator" (≈133, 160) | `fundamentals` accel proxy (labeled) | Concept faithful (1.3× constant is a proxy — ❓). |
| Earnings: reaction > the numbers, gap-up only | "reaction… even more important than the numbers"; "gaps down 10%… we don't want it" (88, 157, 159) | `earnings_watch.passes_gap_filter` (gap>0); `not_earnings_gap_down` prerequisite | Verbatim philosophy. |
| Gap-down = AVOID for longs | "we simply don't want it" (157) | `build_earnings_down` AVOID panel | Defensive treatment faithful (short side omitted — see ❌). |
| Entry = buy-stop 1 tick above pivot | "buy stop 1 penny above the new high" (189) | `entries.compute_setup` trigger+0.01 | Verbatim. |
| Stop: whole → −0.11 (.89) and half → −0.01 | "$45.19 → stop $44.89… extra 10 cents"; "$45.56 → $45.49" (150) | `stop_offset_whole=0.11`, `stop_offset_half=0.01` | Both worked numbers match exactly. |
| Stop hugs the immediate pivot, not the full base low | "low of day stop"; inside-day low / prior low (188, 218) | `stop_lookback=3` | Faithful. |
| No fixed %-price stop cap | book caps risk via position size, not a price-% | `evaluate_gates` imposes none | Correctly absent. |
| R:R ≥ 5:1 entry floor + trim at 3:1 / 5:1 | "at least 5x your risk" (26,149,210); "trimming at 3:1 and 5:1" (211) | `min_reward_risk=5.0`, `trim_levels=(3,5)` | Exact; correctly separates the 5:1 *entry* floor from the 3:1 *trim*. |
| IPO price-discovery thesis + $10 / 300k | "When an IPO breaks its initial IPO high… no resistance" (168-169); "Over $10 / 300k" (171) | `ipo.py`, `IpoConfig` | Core thesis + screen faithful. |
| Targets / Focus / Earnings three-list split + sizes | Targets ~100-200; Focus 10-15; separate earnings list (153-155) | `OutputSizes` targets 100-200, focus 10-15; `earnings_watch` | Architecture **and** caps match. |
| "Layers of probability" enumeration + prerequisites | edges to stack listed; "everything starts with the chart"; gap-down kills it (178-185) | `scoring.LAYER_NAMES` + `chart_ok`/`not_earnings_gap_down` prerequisites | Layer set + the two hard prerequisites map cleanly. |
| Theme baskets (4 specialized), ranked by ETF-RS vs SPY | SMH/SOXX; URNM/NLR/URA; AIQ/BOTZ/ROBO; TAN; "ETF strength vs S&P" (122-123) | `THEME_ETFS` (ticker-for-ticker) + SPY benchmark | Exact on the four specialized baskets. |
| Industry/group RS top 10% (as the metric) | "Group/Industry RS Rank in top 10%" (121) | `TOP_INDUSTRY_FRAC=0.10` | Value faithful (its elevation to a *hard gate* is the issue — see 🔴). |
| Distribution-day count / follow-through-day correctly **absent** | book reads the peak qualitatively ("distribution begins… breakout failure galore", 43); never uses an O'Neill FTD/distribution count | not implemented | Faithful by omission — adding them would import rules the author never prescribes. |
| Supply/demand + accumulation reading | "limited supply and high demand"; "New highs… under accumulation" (19, 29) | universe gates + RS + near-highs bias | Concept covered (book prescribes no indicator line). |
| Position sizing in % not $ ("bullets not dollars") | "think in bullets/percentages, not dollars" (238) | no $-P&L modeling | The *mindset* is honored (but the %-risk **formula** is missing — see ❌). |

---

## ⚠️ IMPLEMENTED WITH MINOR DEVIATION (documented / justified)

1. **RS = labeled proxy, not IBD's RS Rating.** Book names IBD "RS Rating > 90" (PDF-121); code computes a front-weighted trailing-return percentile, **explicitly labeled "NOT IBD's"** (`config.py`, `rs_rating.py`). Threshold value (90) faithful; metric is a transparent proxy. Already in the deviations log.

2. **20 EMA → 20-day SMA proxy on the offline/snapshot path.** Book is explicit the 20 is an **EMA** (PDF-50); the Finviz-snapshot path substitutes SMA20 (`regime.py`, `stage_trend.py`), refined to a true EMA cross only on the live OHLCV path. Self-documented. Minor.

3. **65-minute intraday timeframe not implemented.** Book prescribes **weekly → daily → 65-min** top-down and calls full three-frame alignment "the most powerful technical layer of probability" (PDF-128/129/131). Code does **weekly + daily only** (`timeframes.py`), documented as a free-data limitation. Honest and matches the book's own rationale for 65-min bars — but means the engine can never reach the book's literal "full continuity." Cross-listed under ❌ as a genuine gap; bucketed here because it is documented.

4. **Regime adds QQQ; book examples use SPY only.** Book's regime trio (200 SMA / 50 SMA / 20 EMA) is illustrated on **SPY** (PDF-57+); code uses **SPY + QQQ** with a conservative "any benchmark losing a level pulls the read down" rule (`regime.py`) that is itself not in the book. Reasonable hardening; not book-specified.

5. **Falling-wedge extra confidence + 50-SMA in stage stack.** Code's `detect_falling_wedge` + the `50>200` requirement in Stage 2 use book MAs but in combinations the book doesn't spell out for those exact purposes. (The wedge *slope* condition is a sharper issue — see 🔴 #2.)

6. **Six broad SPDR sector themes added** (XLK/XLE/XLF/XLY/XLV/XLI) beyond the book's four named baskets; book references "ETF sector flows" generically (PDF-121) but doesn't enumerate them. EV theme the book names (PDF-120) is **not** present. Minor.

7. **Gap-down treated as AVOID only.** Faithful to the book's "don't want it" for longs (PDF-157), but the book also describes a first-class **short** setup ("Earnings Flags to the Downside", PDF-110-111) that the long-only scanner intentionally omits. Justified if long-only by design; cross-listed under ❌.

---

## 🔴 IMPLEMENTED WITH UNDOCUMENTED DEVIATION (needs review / fix)

1. **0-100 score, all layer weights, and tier bands (80/65/50) have no numeric basis in the book.** The book grades **qualitatively** — "stacking the most layers", "an A+ setup" (PDF-178-185) — and assigns **no weights, no points, no 0-100 scale, no tier ranges**. Our 7-module weighting (25/25/18/12/10/5/15), the ×100/110 normalization, and Tier 1/2/3 = 80/65/50 (`scoring.py`, `config.LayerWeights`) are entirely an engineering construct. *Not a contradiction* — the book invites "grading" — but it must be presented as a **house calibration**, not book-derived. **Severity: medium** (it drives the whole ranking).

2. **`detect_falling_wedge` slope condition is the opposite of the book's literal wording.** Book: "the **slope of the lows is steeper than the highs**" (PDF-92, i.e. lows fall faster). Code requires `lo_slope > hi_slope` (lows fall *slower*). The code uses the conventional *converging* falling-wedge geometry (arguably more correct), but it contradicts the book's literal text. **Worth a human eyeball.**

3. **Stage-2 definition substitutes an MA-stack for the book's Weinstein criteria; the Stage 1→2 transition rule is unimplemented.** Book Stage 2 = price above a **rising 30-week / 200-day** MA + **breakout above the base on volume** + **positive/turning RS** (PDF-71/73/74). Code uses `price>SMA50>SMA200 + "riding the 20 EMA"` (`stage_trend.py`) — it drops the *slope/"turns up"*, the *base-resistance-break-on-volume*, and the *RS-flip* criteria, and conflates the 20-EMA entry tool with stage classification. The explicit 3-part transition trigger (PDF-74) is **MISSING** as an event.

4. **Industry-RS elevated from advisory to a HARD gate.** Book language is "**Prioritize**… screen for" top-10% / "top 5-10 groups" (PDF-121/122) — advisory ranking, never "reject a name for failing it." Code makes it a hard pass/fail gate (`pipeline.build_targets`, bypass `--no-industry-gate`). The bypass flag mitigates, but the default elevation of strictness is undocumented vs the book. (Book also gives a **conflicting** figure — top-10% vs "top 5-10 groups" — see ❓.)

5. **Measured-move (prior-advance/flagpole) price target is invented; the book prescribes *no* price target.** The strings "measured move", "prior advance", "base depth", "price target" **do not appear in the book** (zero hits). The book takes profit by **R-multiple trims (3:1/5:1) + EMA trailing stop** (PDF-211-221), never a projected target. Our `compute_setup` projects the prior advance and **uses it to pre-qualify R:R ≥ 5:1** (`entries.py`). Direct answer to the brief's Q3: the book prescribes **neither** prior-advance nor base-depth — it has no projection at all. **Severity: material** (it gates Focus inclusion). Reasonable as a house heuristic (you need *some* forward reward estimate to compute R:R pre-trade), but it should be labeled as such, not presented as the book's method.

6. **Quarter-number stop offset −0.06 is not in the book.** Book names "whole, half **and quarter** numbers" as liquidity zones (PDF-149) but gives a worked offset only for whole (−0.11) and half (−0.01). The −0.06 (e.g. 45.25→45.19) is a defensible interpolation with **no textual basis**.

7. **Bull-trap filter `change ≥ gap × 0.5` is a house heuristic.** Book describes the bull-trap concept and "gaps down 10% → don't want it" (PDF-79/157) but gives **no "close must hold ≥ half the opening gap" formula**. The `gap>0 AND change>0` part is well-supported; the **50%-of-gap cutoff is invented** (directionally sound).

8. **Earnings-watch 7-day *active floor* contradicts the book.** Book: "Sometimes the best ones don't wait" and MSFT broke out after only **~4 days** (PDF-89/90). Code's `WINDOW_START_DAYS=7` suppresses anything before day 7 (`earnings_watch.py`). The lower bound has no book basis and excludes the fast-breakout case the book highlights. (Upper bound — see ❓.)

9. **IPO "initial high" = max of first 5 bars + 2% near-band — constants not in book.** Book defines the level qualitatively as "the **initial IPO high**" (PDF-168-169), not a 5-day max; some examples imply a broader/longer base high. The 5-bar window and 2% band (`ipo.py`) are reasonable engineering choices with no textual basis.

10. **5-state regime + position-sizing exposure ladder are not the book's model.** Book gives **four descriptive market-cycle stages** (Expansion/Peak/Recession/Trough, PDF-43) — not a regime *filter* — and only **qualitative** exposure language ("be most aggressive", "ease off the gas", "don't go all-in at once", PDF-43/44/63). Our five states (BULL/NEUTRAL-BULL/NEUTRAL-BEAR/BEAR/VERY-BEAR) and the **Full/Two-Thirds/One-Third/Minimal/Cash = 100/66/33/10/0%** ladder (`regime.py`) are entirely a construct. Mitigated by the in-code label "a SUGGESTION, not a prescription," but it should not read as book-derived sizing. **The book's actual sizing method is the 0.5%-risk formula — see ❌ #1.**

11. **Docstring overstates the weighting.** `scoring.py` claims time-frame continuity and beach-ball are "weighted heaviest," but both sit at raw 10/110 while Compression and Trend/Structure carry **25** each. The book calls **time-frame continuity** "the most powerful technical layer of probability" (PDF-129) — yet it is mid-pack in our weights **and** structurally incomplete (no 65-min). Internal inconsistency + a real under-weighting vs the book's emphasis. Same under-weighting applies to **S/R flips**, which the book calls "one of the most powerful signals" (PDF-77) but we weight at 5/110.

---

## ❌ MISSING — NOT IMPLEMENTED AT ALL

1. **Position-sizing formula (the book's single most quantitative rule).** Book: "I typically **risk 0.5% of my account per trade**", down to 0.1-0.2% on cold streaks, with a linked spreadsheet computing **share count = (account × risk%) ÷ per-share risk**, and a ~**5% aggregate portfolio-heat** cap across 4-10 names (PDF-232-234). We compute per-share `risk` but **no share count, no %-risk, no heat cap** — we substitute a regime exposure label. `shares = floor(account × risk% / risk)` would close this with full fidelity. **Highest-value gap.**

2. **Exit / trade-management discipline.** Book is detailed: trailing stop = **close below the 10 EMA** (20 EMA for slower leaders, 5 EMA in parabolics), trim 10-20% into 3:1/5:1, **trim into resistance**, and the **15-minute pivot-low rule** on entry day (PDF-211-226). The scanner is entry-only and implements none of the sell side. The book's largest coherent body of rules we don't touch. (Scope call if the product stays entry-focused — but worth surfacing as flags.)

3. **Climax / blow-off trim signal — an explicit *numbered* rule.** "As a rule of thumb, if price extends **20% above the 5 EMA** after such a run, that is your cue to begin aggressively trimming" (PDF-220-221). Trivial to add as a deterministic warning: `(price − EMA5)/EMA5 ≥ 0.20`. **Low effort, high fidelity.**

4. **Cup & handle detector.** A book-named **core** base-breakout pattern with three pages of psychology (PDF-82/84-86). We have **no geometric detector and no Finviz-confirm mapping** (no `cup_and_handle` key in `FINVIZ_CONFIRMS`). Clear gap.

5. **Inverse head & shoulders detector.** Book-named bullish-reversal pattern with worked examples (PDF-102-105). No detector and not in `FINVIZ_CONFIRMS`. (The book uses H&S mostly as a topping/avoid signal, so lower priority than cup & handle.)

6. **"Earnings Flags to the Downside" short setup.** Book treats the gap-down event as a real **short** setup (gap down → rising bear channel → breakdown trigger, PDF-110-111), not merely an avoid. We only AVOID. Out of scope if long-only.

7. **Low-float flag.** Book: "**Low float + high volume = explosive price action**" (PDF-19) — a one-line stated criterion. No float gate/flag in the scanner. Minor; data-dependent.

8. **Failed-pattern setups** ("the most bullish setups are failed bearish ones" — failed rounded top / failed H&S, PDF-105-108). Described with a MU example; no detector. Harder to operationalize; arguably out of core scope.

9. **IPO offering-range demand signal** ("priced at/above the top of the offering range", PDF-170). Not captured (offering-range data isn't in the Finviz screen). Data-limited.

10. **Post-trade journaling / analytics** (Ch. 28, called "the #1 key", PDF-244-246). Legitimately **out of scope** for a scanner — a trader-workflow discipline, not a screening signal. Noted for completeness.

---

## ❓ AMBIGUOUS IN BOOK (our interpretation noted)

1. **"Near 52-week highs" — no percentage given.** Book says "Price Near 52-week highs" (PDF-121) but never quantifies. We use **≤ 10% below high** (`max_pct_below_high=10`). Defensible default; **not specified in book.**

2. **Industry-group cutoff — book contradicts itself.** "top **10%**" (PDF-121) vs "top **5-10 groups**" by count (PDF-122). We implement top-10-percent. Reasonable, but the book is internally inconsistent.

3. **Earnings tracking window — book is soft.** "a few weeks", "in the coming weeks" (PDF-89/158); the only hard adjacent number is "1-8 weeks" for high-tight flags (PDF-99). We use **1-4 weeks** (`WINDOW_END_DAYS=28`). A name flagging in week 5-8 would be expired. Our window is a reasonable but tighter-than-loosest reading. (The 7-day *floor* is a firmer issue — 🔴 #8.)

4. **Acceleration multiplier.** Concept is "top fundamental indicator" (PDF-160), but the **1.3×** (QoQ ≥ 1.3 × 5-yr rate) constant is ours — book gives no number. Labeled "(proxy)".

5. **Flat-base depth / near-high caps.** Book gives the 6-week ideal and volume signature but **no numeric depth %**; our 15% depth and 10% near-high caps are house defaults.

6. **Continuation-flag constants** (`detect_flag`: 20% pole, 12-bar pole, 12% flag depth) and **high-tight-flag 25% max depth** — book says only "tight/shallow", no numbers. Reasonable defaults.

7. **"Slingshot" and "VCP" are not named in the book.** "Slingshot" has **zero** occurrences; the rule (>50% advance / lost-then-reclaimed 50 SMA on 1.3× vol) is project-invented though thematically consistent with the book's 50-SMA-reclaim psychology. "VCP" is also absent, but its **concept** (contraction→expansion, "coiled spring", PDF-32-35) *is* the book's thesis and is captured by `atr_compression`. Both should be presented as house constructs, not book patterns. (Slingshot is listed sharper under 🔴-adjacent; kept here because the *concept* is book-grounded.)

8. **IPO recency window.** Book's screen lists "last month/quarter/**year**" as options (PDF-170-171) without prescribing one; we pick "last year" (the widest). Defensible.

---

## Suggested Phase-11 shortlist (from the gaps above — not done here)

| Priority | Item | Why | Effort |
|---|---|---|---|
| 1 | **0.5%-risk share-count sizing** (+ streak throttle, ~5% heat cap) | book's most explicit quantitative rule; we have everything but the formula | low |
| 2 | **Climax trim flag** `(price−EMA5)/EMA5 ≥ 0.20` | explicit numbered book rule; deterministic | low |
| 3 | **Label scoring/tiers/regime-exposure as house calibration** in UI + deviations log | they have no book numeric basis (🔴 #1, #10) | low |
| 4 | **Re-weight time-frame continuity + S/R flip up** (or fix the docstring claim) | book's "most powerful" edges are mid/low-weighted (🔴 #11) | low |
| 5 | **Cup & handle** (Finviz-confirm at minimum, geometric ideally) | book-named core pattern, currently absent | medium |
| 6 | **Re-examine the measured-move target** (label as heuristic; consider next-resistance "look-left") | book prescribes no projection (🔴 #5) | medium |
| 7 | **Falling-wedge slope** — confirm intended geometry vs book text (🔴 #2) | literal-text contradiction | trivial |
| 8 | **Earnings window 7-day floor** — allow day-1+ active (🔴 #8) | excludes the book's fast-breakout case | trivial |
| 9 | EMA-trail exit ladder as flags; inverse-H&S; low-float flag | book-described, lower urgency | medium |

---

*Read-only audit. No code modified. `make test` re-run after the audit: see session log.*
