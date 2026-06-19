# ZEF BOM — Naming Rules (current implementation)

This is the full set of part-numbering and naming rules the app applies today, end to
end — from importing a Miro OPML, through review, to the catalog and edits. It is
generated from the actual code behaviour. Mark up anything you want changed and we'll
update the rules and this document together.

## 1. Anatomy of a part number
Every code is **MODULE + 3 digits + TYPE**, e.g. `AEC066A`.

- **MODULE** — 2–5 uppercase letters. The "system" the part belongs to (`AEC`, `DAC`,
  `DRY`, `FM`, `MS`, … or a longer one like `MDAC`), **or a universal**: `UN` (shared
  across systems) or `UNP` (universal power; replaces the deprecated `POW`). Both
  universals are *sticky* — see §6.
- **3+ digits** — a sequence number, zero-padded to **at least** three (`001`, `066`). A
  module normally stays at three digits, but once it crosses 999 the number simply widens —
  `AEC999A` → `AEC1000A` → `AEC1001A` … There is no upper limit; the width grows as needed.
- **TYPE** — `P` (a **part** = a leaf, no children) or `A` (an **assembly** = has children).
- **New number allocation** — the next free number in a module = (highest existing number
  in that module) + 1, padded to a minimum of three digits.

## 2. Importing an OPML (Miro) file — how each node is classified
When you upload, every node is resolved against the catalog in this order:

1. **Known part** — the node's number exists in the catalog *and* the name matches → **use
   the existing item** (just links it into the tree). No change.
2. **Number drifted (name match — your choice)** — the node's number doesn't line up, but
   its **name uniquely matches one catalog item**. The review screen lists these under
   **"Matched by name"** with a per-row toggle, defaulting to **Merge** — re-point the node to
   that catalog item (adopt its number, no duplicate). Flip to **Create new** if it's
   genuinely a *different* part that happens to share the name (it gets a fresh code in its
   own module; the catalog item is left untouched). This untangles a Miro-vs-catalog numbering
   drift while keeping you in control of every merge.
3. **New part** — the number and name are both new → created, taking the number Miro gave
   it (or an allocated one if it was unnumbered).
4. **Number collision (needs your choice)** — the number already belongs to a *different*
   catalog part, and the name isn't found anywhere. The importer will not guess.
5. **Needs review (needs your choice)** — the importer couldn't place it: the module can't
   be determined, the type (part/assembly) is unclear, the name matches **two or more**
   catalog items, or its parent is itself unresolved.

**Module markers written into Miro names.** Translating from Miro isn't always clean, so a
node whose name carries a marker prefix is re-homed on import:
- `… : Pow: <name>` → module **`UNP`**, name becomes `<name>` (marker stripped). `POW` is
  deprecated to `UNP`.
- `… : UN: <name>` → module **`UN`**, name becomes `<name>` (this node only).
- The original code's number is dropped; a fresh number is taken in the universal module
  (or it matches an existing universal item of the same name).

**POW inheritance.** A `Pow:` node is a *power sub-tree*, so the `UNP` flows **down** to its
descendants, with two stops:
- A descendant that is already **universal** (`UN`/`UNP`) stays as it is.
- A descendant whose **name starts with a system word** (`AEC`, `DAC`, …) keeps that system
  and **halts** the flow for its own sub-tree.
- Example: under `AEC144A: Pow: Electronics` → `Electronics` and `Sensor Cable Tray Horizontal`
  (no system word) become **UNP**; `AEC Power ECU` / `AEC Solar Through Panel` (start with
  "AEC") stay **AEC**; the shared `UN…` fasteners stay **UN**.

**Anchor on the top-most assembly (the key rule for an un-coded BOM).** The importer reads
the system from the *top-level* node and flows it down — it does **not** ask about every node:
1. The top assembly's system = its code if it has one → else a **known system word at the
   start of its name** (`Mdac Inimini system` → `MDAC`; any module in the catalog or in
   *Admin → Modules* counts) → else it's the **one** thing you're asked: *"which system is
   this BOM?"* (a dropdown of system codes).
2. Every bare-named descendant **inherits** that system (`stripper`, `absorber`, `sump` →
   `MDAC…`). Universals still win (`POW …`/`UN …` → `UNP`/`UN`), and a descendant that itself
   starts with a *different* system word keeps that system.
3. While the top system is still unknown, the descendants are held as **"will inherit"** (not
   asked about one-by-one); once you pick the system, they all resolve. Only genuine **name/
   code discrepancies** (a reused number, etc.) are surfaced for you to resolve alongside it.

For **unnumbered** nodes specifically:
- **Module** is inferred as: an explicit prefix if present → otherwise it **inherits the
  system of the assembly above it** (per the anchoring rule) → otherwise, only if even the
  top assembly is undetermined, it waits on that one "which system?" choice.
- **Type** is: explicit if given → otherwise has-children = `A`, leaf = `P`.

## 2b. Update vs. new variant (what kind of upload this is)
Every upload is one of two kinds, chosen on the upload form:
- **Update / add to the BOM** (default) — the OPML is matched against the live BOM and applied
  in place: existing parts are reused, renames/qty/new parts flow in (sections 2 & 4).
- **New variant (a separate BOM)** — the OPML becomes a **distinct top-level BOM that coexists**
  with the original (e.g. *prototype v2* alongside *v1*). The original is left completely
  untouched. Specifically:
  - **System parts get fresh codes**, keeping their names (v2's `Pump` is a new `AEC…`, not v1's).
  - **Universals are shared** — a `UN`/`UNP` part (a screw, a wire) is the *same* commodity across
    variants, so it's reused by name, never duplicated.
  - The variant's **system** is read from the top assembly exactly as in §2 (its code → a system
    word in its name → else the one *"which system?"* question), and that system codes the
    whole variant.
  - A variant is always top-level (it is its own tree root); it is never attached under a parent.

## 3. The review screen — what you resolve, and the options
- **Matched by name** → **Merge** (default — link to the existing catalog item, no duplicate)
  · **Create new** (this is a different part that shares a name — allocate a fresh code).
- **Number collision** → **Add as new** (a fresh number is allocated; the existing catalog
  item keeps its name) · **Rename existing** (update that catalog item's name to Miro's) ·
  **Skip**. *Default = Add as new.*
- **Needs review** → **Create new** (you pick the module + part/assembly; a number is
  allocated) · **Match existing** (search and pick a catalog code to link to) · **Skip**.
- An explicit **Create new** always wins over a name match (so a duplicate-ish name does
  not get stuck in review).
- Nothing is written until you click **Approve**.

## 4. What Approve actually writes
Atomically, in one transaction: create new items · rename items whose name changed · add /
remove / change-quantity on the links · flag the root as **top-level** (even if the item
already existed) · then run the **naming engine** (section 6) to make everything consistent.

## 5. Catalog — adding & editing
- **Add a new item** — free-text name + type (part/assembly) + **module** (default **UN**).
  - **No accidental duplicates** — if a live item already has that name (compared after
    normalization, so `O ring` = `O-Ring`), the app blocks the add and tells you which code
    already holds it. You can confirm "add anyway" to create a deliberately separate part.
  - **UN** = universal → it **keeps the UN code wherever it's used**.
  - A specific module (e.g. `AEC`) → follows the usage rule below (stays `AEC` while only
    in AEC; becomes `UN` if shared).
  - A catalog item that isn't in any BOM yet **keeps its chosen code** until it's used.
  - New module names (e.g. `MDAC`) are added in **Admin → Reference data → Modules** and
    then appear in the picker.
- **Editing an item** (name, weight, supplier, materials, CAD URL, notes, etc.) — saved to
  the item and logged in history.
- **Changing the module / code by hand** — the add/edit tab has a **Module** dropdown. It
  offers only **`UN`, `UNP`, and the item's parent-assembly module(s)** — so an `AEC`
  assembly can never be given a `DAC` part. Changing it does an **atomic re-code** (every
  link, cost, history entry and the catalog are repointed) and is logged. If the target
  number is already taken (e.g. `AEC050P → UN` but `UN050P` exists), a fresh number is
  auto-allocated so there's no overlap.

## 6. The naming engine (runs automatically after any structural change)
Two passes, in order:

**Pass 1 — TYPE follows children:**
- Has at least one live child → it's an **assembly** → code ends in **A**.
- No children → it's a **part** → code ends in **P**.
- A part that gains children is renamed `…P → …A`; an assembly that loses all children is
  renamed `…A → …P`. (If the target code is already taken, it keeps its number but still
  switches type.)

**Pass 2 — MODULE follows usage:**
- Used in exactly **one** system (one top-level BOM) → takes **that system's** module code.
- Used in **two or more** systems → becomes **`UN`** (never `UNP`).
- **`UN` and `UNP` are both sticky** — once a part is `UN` or `UNP`, it stays that way (a
  deliberately universal part never re-codes to a single system). `UNP` is only ever set by
  the import marker (§2) or a manual edit (§5).
- **Roots** (top-level BOMs) and **catalog-only** items (not used in any BOM) are never
  re-coded.

**When the engine runs:** after approving an upload, adding a child, attaching a sub-BOM,
and archiving / restoring / purging an item or link.

## 7. Name text normalization (applied to every name)
Collapses extra spaces, applies smart Title Case, and fixes known tokens — e.g.
`o ring` / `oring` → **O-Ring**, `c tube` → **C-Tube**, and uppercases acronyms (`AEC`,
`UN`, `DRY`, `CAN`, `PCB`, `ECU`, `NTC`, `IP`, `EPDM`).

## 8. Safety rules
- **Atomic re-code** — when a code changes, every reference is repointed in one go: BOM
  links (both parent and child sides), decided costs, cost evidence, **assembly labour
  times**, custom field values, and change history — and the rename is logged. Nothing dangles.
- **Attachments survive a re-code** — a part's Drive folder is located by its **stable folder
  id** (carried on the item), not by a folder named after the code, so re-coding an item keeps
  it pointed at the same attachments. The folder's name is re-synced to the new code the next
  time attachments are opened or uploaded.
- **No loops** — a link that would make an item contain itself (directly or indirectly) is
  rejected.
- **Archived items** are ignored for "has children", usage, and the tree.
- **Full audit trail** — every create / rename / type-change / re-code is written to change
  history with who, when, and why.
