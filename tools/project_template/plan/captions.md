<!-- One block per float. Document order IS manuscript order.
     Heading:  ## Figure N — <the folder the float lives in>
     Line 1:   **A bold sentence stating the claim this float supports.**
     Rest:     Panel keys and plotting detail: what is shown, n, tests, ref lines.

     This file is the single source of truth: plan/preview.html, the Word
     floats, and the manuscript all read it through read_captions(). Edit a
     caption once and it propagates to all three.

     ONE FLOAT, ONE FOLDER. Figure 1 is plan/figures/Fig01/ and Table 1 is
     plan/tables/Table01/. The heading names that folder, and it is matched by
     NUMBER, not by spelling — so `Fig01`, `Fig1` and `Fig01_yield_by_catalyst`
     all resolve to the same folder. Add a slug to a folder name whenever it
     helps you read the file tree; nothing here has to change.

     Supplementary floats use S numbers ("## Figure S1 — FigS01") and are
     routed to the supplementary document automatically.

     RENUMBERING. The folder name is the number, so moving Figure 3 to
     Figure 2 is renaming its folder and editing the number in its heading
     here — nothing inside the folder moves. To swap two floats, or to
     renumber a run of them, use:

       python tools/scaffold.py float renumber <project> --from 3 --to 2

     which does both halves and shifts everything else out of the way.

     Copy one of these to start:

       ## Figure 1 — Fig01
       **Catalyst B doubles the yield of catalyst A across the whole temperature range.**
       (A) Yield against temperature for each catalyst. (B) Turnover number at
       80 C. n = 3 independent runs per condition; error bars, SD; Welch t-test.

       ## Table 1 — Table01
       **Reaction conditions are matched across catalysts except for the catalyst itself.**
       Mean +/- SD of three independent runs.

     Delete this comment once you have the hang of it. -->
