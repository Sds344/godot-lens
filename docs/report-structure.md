# How GameDevBench writes it up, and what to borrow

Reference notes on the structure and argumentative moves of
[GameDevBench](https://waynechi.com/gamedevbench) (Chi et al., ICML 2026,
[arXiv:2602.11103](https://arxiv.org/abs/2602.11103)), recorded so this project's
own write-up does not have to reinvent the genre.

## Their section structure

```
Abstract
1  Introduction            why this domain · why this engine · what we built · what we found
2  Benchmark Construction  Stage 1 data prep → 2 task construction → 3 refinement → 4 human annotation
3  GameDevBench            3.1 task categories   3.2 features
4  Evaluation              4.1 feedback methods   4.2 results   4.3 error analysis
5  Related Works
6  Conclusion
A–J Appendices             prompts · task examples · statistics · case study · failure analysis · contamination
```

## The five argumentative moves worth copying

**1. Justify the domain with numbers, not adjectives.** They do not say game
development is hard; they say solutions average 4.7 files and 114 lines, "more
than three times SWE-Bench". A claim with a number attached is checkable.

**2. Justify the substrate with engineering reasons.** Four explicit reasons for
Godot: MIT licence, release counts on Steam (770 in 2024, 1185 in 2025),
similarity to Unity, and — the one that matters most — **"Godot projects can be
represented in code, which makes it simple to extend existing LLM agent
capabilities without having to construct specific tool-use APIs"**. That last
reason is the same one this project relies on, and it is worth stating equally
plainly.

**3. Separate what was built from what was measured.** Construction gets its own
section (2) with four named stages; evaluation gets another (4). The reader can
tell which claims are about the artifact and which are about results.

**4. Report the uncomfortable number next to the flattering one.** The abstract
leads with "best agent solves only 53.8%" — the limitation is the headline, not a
footnote. Their §4.3 then devotes a subsection to *failure* patterns.

**5. Pre-empt the obvious attack.** Appendix J is a data-contamination analysis:
they check whether models memorised the tutorials (ROUGE-L 0.06, BLEU-4 0.02) and
report that they did not. A benchmark built from public tutorials invites exactly
this objection, and they answer it before it is raised.

## Their results presentation

Table 2 is a single flat table: harness × model × feedback configuration ×
`pass@1` with a 95% CI. No per-task listings in the body — those live in
`results/`. The CI comes from **333 tasks, one run each**, so the granularity is
per-task, not per-repeat.

Two features of that table are worth imitating:

- **A row per configuration**, including the ablations (screenshot only, video
  only, both, neither), so the contribution of each mechanism is visible without
  cross-referencing prose.
- **Bold for best, italics for second best.** Trivial, but it means the reader
  does not have to hunt.

They also split results by difficulty (easy/hard) and by category (Table 5,
Figure 5), which is how they support the claim that *multimodality* is the
bottleneck rather than difficulty in general. **The pattern across subgroups, not
the aggregate, is the evidence.**

## What does not transfer

- **Scale.** 333 tasks and eight models. This project has six cases and one
  harness. Any report must say so, and must not imitate their confident tone.
- **Per-task pass@1 with tight CIs.** With six cases the interval is ±28pp at two
  repeats. Reporting a percentage as though it were a measurement would be the
  dishonest move here.
- **Novelty claim.** They are first-in-domain. This project is not; it sits beside
  `godot-mcp` and beside GameDevBench itself.

## Proposed structure for this project's report

Deliberately shorter, and organised around the one thing that is genuinely ours.

```
Abstract            the observation gap · what the layer reports · the B-vs-C result (or its absence)

1  The gap          files are not the game · engine-silent faults · the narrator case as a real defect
2  The observation layer
   2.1 the schema (lens/0.1) and the three-source model
   2.2 facts / meaning / advice are separate layers
   2.3 how the layer refuses to lie (three runtime states; absent ≠ null)
3  What it can see  the coverage measurement: 8/10 fault classes, the 2 blind ones, 0 false positives
4  Evaluation       4.1 arms and why B is the control
                    4.2 the per-case pattern (the result)
                    4.3 hack rate  ← the distinctive metric
5  Boundaries       multimodal half out of reach · model coverage · the self-grading threat
6  Related work     godot-mcp (hands) · GameDevBench (produce) · this (perceive)
A  Reproduction     every command, every fingerprint
B  Failure analysis the graders' own history of false positives
```

The section that has no counterpart in their paper is **§5 and appendix B**:
this project's most transferable finding is not a capability but a **failure
analysis of checkers** — two graders that passed silently-broken projects, and the
self-test discipline that caught them. That is the contribution that generalises
beyond Godot, and the write-up should lead with it rather than bury it.

## Citation

```bibtex
@inproceedings{chi2026gamedevbenchevaluatingagenticcapabilities,
  title={GameDevBench: Evaluating Agentic Capabilities Through Game Development},
  author={Chi, Wayne and Fang, Yixiong and Yayavaram, Arnav and Yayavaram, Siddharth
          and Karten, Seth and Wei, Qiuhong Anna and Chen, Runkun and Wang, Alexander
          and Chen, Valerie and Talwalkar, Ameet and Donahue, Chris},
  booktitle={International Conference on Machine Learning (ICML)},
  year={2026},
  eprint={2602.11103},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
}
```
