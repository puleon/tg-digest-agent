# D6 — Deduplication: what a "duplicate" is, and how well the cheap cascade finds it

**Question.** SPEC §6.3 asks for a cascade — exact text, identical file, pHash, embeddings
inside a 7-day window, forwards — and a pairwise precision/recall on 80–120 hand-labelled
pairs. How precise are the cheap stages on this corpus, and which thresholds do the labels
support? (SPEC §9 D6.)

**Setup.** 18 987 posts (25 939 message rows; an album is one post), 22 888 with media.
Signatures (`tgdigest dedup signatures`): normalized text key ≥ 20 chars → sha1 (14 080
posts), sha256 of the file (22 681), 64-bit DCT pHash of the thumbnail (22 675). Clusters are
rebuilt wholesale (`tgdigest dedup run`): union-find over edges, cheapest stage first. The
embedding stage waits for the D7 index; the forward stage links posts forwarded from the same
source message or from a corpus channel (one cluster in this corpus — too rare to measure).

## Labels — 107 pairs, one human, one convention

`scripts/dedup_labels.py sample` draws two kinds of pairs (seed 42): **precision pairs** —
the representative of a cluster against another member, stratified by the stage that joined
it (67); **near misses** — pairs just outside the thresholds, pHash distance 7–12 or TF-IDF
cosine ≥ 0.5 without being clustered (40), for recall. The sheet shows both posts with media
and text; labels are `dup` / `not` / `unsure`. The labels were prefilled by the assistant
with a one-line reason per pair and reviewed by the owner, who corrected 3 of the first 40 and
accepted the rest by analogy — so the convention below is the owner's:

- **dup** — the same item re-posted: a meme re-encoded or resized (with or without a new
  caption), the same teaser/poster under short captions in different channels, a channel
  re-promoting its own item within days (a podcast episode announced, then released, with the
  same cover), a text re-posted verbatim.
- **not** — two channels covering the same news in their own article-length words even when
  the photo is identical; a meme template under a different joke; different issues or
  posters under a templated caption; a press-kit still reused for a different post weeks later;
  a *reply* to a post (a follow-up with its own text).

Three sampled "pairs" were two images of one album and are skipped: a post cannot duplicate
itself (the sampler now excludes them). Twelve pairs were relabelled `dup → not` after the
reply-text repair described below — the sheet had shown the parent's text for both posts.
Final labels: dup 41, not 63.
Files: [`results/labels.csv`](results/labels.csv), per-pair evidence
[`results/features.csv`](results/features.csv) (`scripts/dedup_labels.py features`: pHash
distance, file/text equality, text lengths, TF-IDF and Jaccard overlap, image contrast,
channel, days apart).

## Where the first cascade was wrong

Scored as committed before this experiment, on the labels as first reviewed: **precision
0.765 (52/68), recall 0.981 (52/53)**. Every miss had a visible cause in the features:

| symptom | pairs | evidence |
|---|---|---|
| same press photo under two different stories | 6 | pHash 0, both texts 300–1 000 chars, Jaccard < 0.1, other channel or > 7 days apart |
| near-black trailer thumbnails glued across films | 2 (+1 near miss) | grayscale std 2–7 at 32×32; the old blank gate was std < 2 |
| identical rubric caption over different posters | 1 | `«Title» (year) (by Artist) #PosterPorn`, 59 chars, 51 days apart, pHash 32 |
| a channel's own photo reused for a later post | 1 | same file, 11 days, different 400/560-char texts |
| a gallery post bridging distinct items | 2 | mirf_ru's album {cover, still} matched a cover post *and* a still post of another channel; the two are then one cluster |
| meme template with a different joke | 1 | pHash within 6 of another variant; pHash cannot see text |
| "truncated repost" missed | 1 (recall) | turned out to be a parser artefact, see below |

The dup side is just as informative: the owner counts the same channel's announcement +
release of one podcast episode (same cover, different 1 000-char texts, 3 days apart) as a
duplicate, but two channels' own write-ups of one NASA photo as different posts. Text
similarity alone does not separate these (Jaccard 0.07–0.17 on both sides); channel and time
do.

## Rules derived from the labels (`src/tgdigest/dedup/cluster.py`)

1. **Contrast gate** `BLANK_STD = 8` (was 2): frames below it get no pHash and no file hash.
   Measured on a 3 000-image sample: 0.8 % of media fall under 8, 22 of those 24 are video
   thumbnails; among pHash-clustered posts the share was 2.1 % — dark frames were matching
   each other at random.
2. **Illustration veto** for file/pHash edges: both posts carry text, the texts differ (word
   Jaccard < 0.5), and either they are > 7 days apart or they come from different channels
   with article-length texts (≥ 300 chars each). The veto is a *cannot-link* constraint in
   union-find, so a third post that merely re-posts the photo cannot bridge the two stories
   (that alone was worth 3 points of precision).
3. **Rubric veto** for text edges: a caption < 100 chars over different media, > 7 days apart.
4. **Truncated repost** edge: one text key is a prefix of the other, both ≥ 100 chars. Kept
   only because it is cheap: after the data repair it fires twice in the corpus, both genuine
   (a channel copying a colleague's text and dropping the tail).
5. pHash threshold stays at 6: none of the 17 near misses at distance 7–12 is a duplicate,
   and every false positive at ≤ 6 had a non-pHash explanation above.

## Result

| cascade | clusters | posts in clusters | precision | recall (pooled) |
|---|---|---|---|---|
| as committed, labels as first reviewed | 986 | 2 299 | 0.765 (52/68) | 0.981 (52/53) |
| + contrast gate, vetoes, cannot-link (same data) | 960 | 2 150 | 0.930 (53/57) | 1.000 (53/53) |
| same rules, data repaired, 12 reply pairs relabelled | **774** | **1 721** | **0.932 (41/44)** | **1.000 (41/41)** |

Merges by stage on the repaired data: text 80, text-prefix 2, file 328, pHash 536, forward 1;
321 media edges vetoed as illustrations (19 further merges blocked by the cannot-link), 15
text edges vetoed as rubrics. Cluster sizes: 643 pairs, 103 triples, 28 larger, max 8.

**Ablation** (repaired data, tuning labels): without the illustration veto precision is
0.804 (41/51), without the rubric veto 0.911 (41/45), without both 0.788 (41/52); recall stays
1.000 in every variant. The remaining three errors are the two gallery-bridge pairs (a design
limit of transitive clustering over albums), the meme template (needs the VLM's OCR text — a
natural follow-up once the ingest pass completes: veto pHash matches whose OCR texts differ),
and one borderline pair the owner has not reviewed (a press-kit still under a joke caption).

## Out-of-sample checks

The rules were chosen on the 107 pairs above, so their score is a fit. Two fresh samples,
drawn with `--exclude` so no post overlaps the tuning set, labelled by the assistant under the
owner's convention (owner's review pending; [`results/labels_holdout.csv`](results/labels_holdout.csv),
[`results/labels_vetoed.csv`](results/labels_vetoed.csv)):

| sample | what it measures | result |
|---|---|---|
| hold-out, 52 pairs (seed 7): 32 from the tuned clusters, 20 near misses | precision of what the cascade keeps; recall against near misses | **precision 0.947 (18/19), recall 0.947 (18/19)** |
| 30 pairs the vetoes split apart (together without vetoes, apart with them) | whether the vetoes cut real duplicates | **29/30 correctly apart**; the one miss is a magazine issue announced for pre-order and again on sale 14 days later — the 7-day window is a knob, not a law |

The hold-out cannot see the vetoes' benefit (pairs they split are not in the tuned clusters),
which is why the second sample exists. The hold-out's one false positive is a short caption
over an album sharing a press photo with an article the same day (not vetoed: only one text is
article-length); its one miss is a trailer posted as a bare YouTube link in one channel and as
a still + text in another — no media to match, texts differ: the embedding stage's job.

## Side finding — two parser bugs, ≈ 4 000 rows, found by the features table

Video posts 97–144 days "apart" that were posted the same day, and "verbatim reposts" that
were really replies, both came from `collector/web.py`:

- **Dates.** The parser took the first `<time>` of a message block; for video and GIF posts
  that is the duration overlay (`<time class="message_video_duration">`, no `datetime`), and
  the fallback stamped the collection time. 2 843 of 3 106 animations, 498 photos, 77 videos
  and 101 link previews (3 519 rows) carried 2026-09-19 against ~150 real posts that day;
  every 7-day rule above would have been wrong for them.
- **Reply texts.** A reply block (`a.tgme_widget_message_reply`) quotes the parent under the
  same `tgme_widget_message_text` class, truncated with "…"; taking the first such node stored
  the parent's text for 464 replies. Every "identical text in the same channel a day later"
  pair in the first labelling round was one of these — 12 of the 53 pairs the owner had
  confirmed as duplicates — and the "truncated repost" rule was fitted to the artefact
  (112 merges before the repair, 2 after).

Both are fixed in the parser with regression tests on fixtures, and repaired in place with the
new `tgdigest collector refresh` (re-reads the preview pages without media: 25 816 messages,
40 min per pass through the tunnel, 0 flood waits; repairs dates and texts, refreshes
views/forwards/reactions, drops enrichment rows computed from a stale text — 22). The
negative result is the useful one: labels and cascade agreed on the 12 artefact pairs because
both read the same wrong text, so the 0.930 looked clean; against correct labels the
unrepaired data would have scored 0.719 (41/57).
