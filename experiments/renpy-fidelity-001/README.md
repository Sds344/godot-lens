# Semantic fidelity — does the running game still say what the source said?

This is the study that answers a question the rest of the kit cannot: **the engine
is healthy, but is the game correct?**

Engine health and translation correctness are different properties, and the
difference is not academic. The defect that motivated this work was Ren'Py's

```renpy
define narrator = Character(None)      # script.rpy line 2
```

being emitted by the converter as an `unsupported` node **inside the body of the
`start` label**, whose first statement is at line 11. The generated Godot project
was perfectly valid. Every check in this kit passed. The game was missing a line
of the story.

## Status

Agreement, measured on the reference project at the fingerprint in
`snapshot.json`:

| Instrument | Result |
|---|---|
| executed source spans vs IR | AGREE — 10/10 spans resolve |
| display order and attribution | AGREE — 4/4 displays in order |
| IR gaps | AGREE |
| IR placement | AGREE |

`inspect-ok`-style green means *this* — and it means only that the four checks
below agreed on the executed path. It is not a statement that the project is
correct.

## The four instruments, and why four

Each instrument is blind to what the others see. That is not a design flourish;
it was forced by experiment.

| # | Instrument | Catches | Blind to |
|---|---|---|---|
| 1 | **executed spans** — every source position the runtime executed must resolve to an IR node | a statement the runtime ran that the IR does not model | content the IR *has* but placed wrongly |
| 2 | **display trace** — the IR's promised displays must appear, in order, with the right speaker | wrong order, wrong speaker | content the IR already dropped |
| 3 | **IR gaps** — every non-trivial source line inside the transcribed range must be claimed by an IR node | a line the converter walked past | a line that got a node of the wrong kind |
| 4 | **IR placement** — a body node's source line must not precede its own label | a declaration folded into a statement body | anything requiring a runtime |

### The finding that made instrument 4 necessary

Instruments 1, 2 and 3 were all run against the **real broken IR** from the
converter's history (commit `9b5dd95`) and **all three reported agreement**.

The reason is structural, not a bug in them: the broken IR is a *superset* of
what the runtime executes. It still carries every node the game ran, plus one
extra that the runtime skips because it is marked `unsupported`. Any check that
compares runtime output against the IR is therefore blind to an IR node that is
merely **misplaced** — it can only see nodes that are missing or wrong.

Instrument 4 reads the IR alone and asks a question the runtime cannot answer:
*can this node belong to the label that contains it?* Line 2 cannot belong to a
body whose statements begin at line 11. That is a contradiction inside the IR,
detectable without a runtime and without parsing Ren'Py.

**This is the concrete result of the study**, and it generalises: for a
translation defect, "compare the output against the model" is not sufficient,
because a wrong model can be self-consistent and still contain everything the
output shows.

## Running it

```bash
# pin what is being measured (records the project fingerprint; never edits it)
godot-lens study snapshot --name renpy-fidelity-001

# run all four instruments
godot-lens study run --name renpy-fidelity-001

# verify the instruments themselves, including against the real historical defect
python3 benchmarks/study_selftest.py --real-artifact
```

`study run` exits non-zero on any divergence, so it gates CI the same way
`validate` does.

## Reproducibility

`snapshot.json` records the fingerprint this was measured at, including the
**dirty-file count** of the sibling project at capture time. That number is kept
deliberately: a study that recorded "clean" while measuring a working tree with
uncommitted changes would be claiming a reproducibility it did not have. The
subject project was under active development throughout, which is exactly the
condition this pin exists to survive.

The sibling project is **never modified** — not even tagged, because a tag in
someone else's repository is still a change to it.

## Limits

- **One path.** The runtime executes one branch; an untaken branch is unchecked
  by instruments 1 and 2. Instruments 3 and 4 are static and do cover the whole
  file, which is part of why they earn their place.
- **Conventional layout.** Instrument 2 decides which node is body text and which
  is the speaker plate from vertical position. A game that draws its dialogue
  somewhere unusual would be paired wrongly. Stated rather than hidden.
- **Subsequence matching.** Instrument 2 detects omission and reordering; it does
  not detect a display that appears *extra* times. Widening it needs a stronger
  specification, not a looser matcher.
- **Text only.** Images, audio and positioning are not compared. A faithful
  script rendered with the wrong art passes.
- **The IR is assumed to be the specification.** These instruments check that the
  runtime is faithful *to the IR*. If the converter misunderstood the source and
  recorded that misunderstanding faithfully in the IR, all four agree and the
  game is still wrong. Closing that last gap needs a source-level oracle, which
  is a different tool.
