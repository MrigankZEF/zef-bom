part_number:
  pattern: "^[A-Z]{2,3}[0-9]{3}[PA]$"

naming:
  disallowed_tokens:
    - thing
    - item
    - part
    - unit
  disallowed_characters:
    - "_"
    - "-"
    - "."
  capitalization: title_case

bom_links:
  # strictly greater than zero: a 0-qty link is a data bug — remove the child instead
  quantity_min_exclusive: 0
  prevent_circular_references: true

sourcing:            # decided_costs.make_or_buy — one value per volume tier
  allowed:
    - buy            # off the shelf
    - made-to-order  # our specs, supplier builds it
    - make           # in house
  retired:
    - modified-buy   # migrated to made-to-order in 0009

cogs_facility:
  # the kind decides which rows the matrix has, so it is fixed at creation and a PATCH
  # carrying one is rejected server-side (422), not silently ignored
  kind:
    allowed: [assembly, logistics, field]
    immutable_after_create: true
  code:
    pattern: "^[A-Z]{2,4}-[A-Z0-9]{2,8}$"      # FAC-ASM
  # a row key must belong to the facility's kind (see cogs.KINDS); rejected on write, never
  # stored, because a value no basis handler reads is invisible in every total
  row_key:
    must_belong_to_kind: true

cogs_value:
  volume_tier:
    allowed: [1, 100, 10000]
  # absent row = "not entered" (displays an em dash, contributes 0);
  # stored 0 = "known to be zero". Clearing a cell DELETES it rather than writing 0.
  absence_is_meaningful: true
  scrap_pct:
    min: 0
    max: 95            # 100% would divide the BOM by zero
  facility_item_code:
    pattern: "^[A-Z]{1,2}-[A-Z0-9]{2,8}$"      # A-LINE
