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
