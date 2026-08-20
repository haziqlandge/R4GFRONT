"""Layer 2: what happens between retrieval and answering. Phase 6.

Architecture.md 7 Layer 2 specifies three checks here. One of them ships and
lives elsewhere; the other two were built as measurements first and are
deliberately NOT implemented, because the measurements say they would make the
product worse. This module holds the reasoning so the absence is a decision
rather than an omission.

1. THE CONFIDENCE FLOOR — ships, in answering/router.py.
   `ROUTE_TAU_LOW = -1.103`, fitted on the dev partition by
   scripts/06_calibrate_routing.py. It lives in the router rather than here
   because the same score also picks between the extractive and generative
   paths, and splitting one calibrated number across two modules would let the
   floor and the routing drift apart. ISSUES.md I3 records why the floor cannot
   sit on the dense score at all.

2. THE SCORE-GAP AMBIGUITY CHECK — measured, rejected.
   The idea: when the top two candidates score alike, no single passage is
   clearly the answer, so refuse. It does not survive contact with the data.
   Measured over bench/adversarial.jsonl against the live service:

       gap cut   ambiguous caught   answerable lost
          0.10        2 of 9            1 of 14
          0.50        4 of 9            2 of 14
          0.75        5 of 9            4 of 14
          2.00        8 of 9            6 of 14

   There is no cut that buys ambiguity detection at an acceptable price. The
   distributions overlap outright: a real question, "what happens during a
   docket call in court", has a gap of 0.07, which is SMALLER than the gap on
   the single word "mercury" at 0.08. Any threshold that catches mercury
   refuses the docket question first.

   The honest reading is that a small gap means several passages are similar,
   which happens both when a query is ambiguous and when a corpus simply holds
   several good passages about one subject. The gap cannot tell those apart, so
   it is not a signal for refusal.

   Caveat, stated because the numbers are small: 9 ambiguous and 14 answerable
   cases. The direction is clear and the overlap is not marginal, but this is a
   reason not to ship rather than a precise measurement of the cost.

3. THE LANGUAGE MISMATCH FLAG — rejected on the design, not the data.
   The idea: flag it when the query language and the retrieved passage
   languages disagree entirely. On this corpus that is backwards. Answering a
   Hindi question from the English twin of a passage is the cross-lingual
   retrieval this project has claimed since Phase 1, and it was observed firing
   on live spoken input on 20 Aug: an English question returned a Hindi passage
   at rank 2 beside its English twin at rank 1. A guard here would refuse the
   system's own headline capability.

What actually catches the cases this layer was supposed to catch is the output
guard, which reads the answer rather than the scores. ISSUES.md I26 is the
reason that turned out to be true generally: retrieval confidence answers "is
this question in the corpus" and no threshold on it answers "is this answer
supported".
"""

from __future__ import annotations
